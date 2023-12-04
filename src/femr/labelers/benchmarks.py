"""Labeling functions for OMOP data."""
from __future__ import annotations

import datetime
from typing import Any, List, Optional, Set, Tuple, Union

import pandas as pd
from femr import Event, Patient
from femr.extension import datasets as extension_datasets
from femr.labelers.core import Label, Labeler, LabelType, TimeHorizon, TimeHorizonEventLabeler
from femr.labelers.omop import (
    CodeLabeler,
    WithinVisitLabeler,
    does_exist_event_within_time_range,
    get_death_concepts,
    get_femr_codes,
    get_inpatient_admission_events,
    move_datetime_to_end_of_day,
)
from femr.labelers.omop_inpatient_admissions import get_inpatient_admission_discharge_times
from femr.labelers.omop_lab_values import InstantLabValueLabeler


def identity(x: Any) -> Any:
    return x


def get_icu_visit_detail_concepts() -> List[str]:
    return [
        # All care sites with "ICU" (case insensitive) in the name
        "CARE_SITE/7928450",
        "CARE_SITE/7930385",
        "CARE_SITE/7930600",
        "CARE_SITE/7928852",
        "CARE_SITE/7928619",
        "CARE_SITE/7929727",
        "CARE_SITE/7928675",
        "CARE_SITE/7930225",
        "CARE_SITE/7928759",
        "CARE_SITE/7928227",
        "CARE_SITE/7928810",
        "CARE_SITE/7929179",
        "CARE_SITE/7928650",
        "CARE_SITE/7929351",
        "CARE_SITE/7928457",
        "CARE_SITE/7928195",
        "CARE_SITE/7930681",
        "CARE_SITE/7930670",
        "CARE_SITE/7930176",
        "CARE_SITE/7931420",
        "CARE_SITE/7929149",
        "CARE_SITE/7930857",
        "CARE_SITE/7931186",
        "CARE_SITE/7930934",
        "CARE_SITE/7930924",
    ]


def get_icu_visit_detail_codes(ontology: extension_datasets.Ontology) -> Set[str]:
    return get_femr_codes(
        ontology, get_icu_visit_detail_concepts(), is_ontology_expansion=True, is_silent_not_found_error=True
    )


def get_icu_events(
    patient: Patient, ontology: extension_datasets.Ontology, is_return_idx: bool = False
) -> Union[List[Event], List[Tuple[int, Event]]]:
    """Return all ICU events for this patient.
    If `is_return_idx` is True, then return a list of tuples (event, idx) where `idx`
        is the index of the event in `patient.events`.
    """
    icu_visit_detail_codes: Set[str] = get_icu_visit_detail_codes(ontology)
    events: Union[List[Event], List[Tuple[int, Event]]] = []  # type: ignore
    for idx, e in enumerate(patient.events):
        # `visit_detail` is more accurate + comprehensive than `visit_occurrence` for
        #   ICU events for STARR OMOP for some reason
        if e.code in icu_visit_detail_codes and e.omop_table == "visit_detail":
            # Error checking
            if e.start is None or e.end is None:
                raise RuntimeError(
                    f"Event {e} for patient {patient.patient_id} cannot have `None` as its `start` or `end` attribute."
                )
            elif e.start > e.end:
                raise RuntimeError(f"Event {e} for patient {patient.patient_id} cannot have `start` after `end`.")
            # Drop single point in time events
            if e.start == e.end:
                continue
            if is_return_idx:
                events.append((idx, e))  # type: ignore
            else:
                events.append(e)  # type: ignore
    return events


##########################################################
##########################################################
# CLMBR Benchmark Tasks
# See: https://www.medrxiv.org/content/10.1101/2022.04.15.22273900v1
# details on how this was reproduced.
#
# Citation: Guo et al.
# "EHR foundation models improve robustness in the presence of temporal distribution shift"
# Scientific Reports. 2023.
##########################################################
##########################################################


class Guo_LongLOSLabeler(Labeler):
    """Long LOS prediction task from Guo et al. 2023.

    Binary prediction task @ 11:59PM on the day of admission whether the patient stays in hospital for >=7 days.

    Excludes:
        - Visits where discharge occurs on the same day as admission
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        self.ontology: extension_datasets.Ontology = ontology
        self.long_time: datetime.timedelta = datetime.timedelta(days=7)
        self.prediction_time_adjustment_func = move_datetime_to_end_of_day

    def label(self, patient: Patient) -> List[Label]:
        """Label all admissions with admission length >= `self.long_time`"""
        labels: List[Label] = []
        for admission_time, discharge_time in get_inpatient_admission_discharge_times(patient, self.ontology):
            # If admission and discharge are on the same day, then ignore
            if admission_time.date() == discharge_time.date():
                continue
            is_long_admission: bool = (discharge_time - admission_time) >= self.long_time
            prediction_time: datetime.datetime = self.prediction_time_adjustment_func(admission_time)
            labels.append(Label(prediction_time, is_long_admission))
        return labels

    def get_labeler_type(self) -> LabelType:
        return "boolean"


class Guo_30DayReadmissionLabeler(TimeHorizonEventLabeler):
    """30-day readmissions prediction task from Guo et al. 2023.

    Binary prediction task @ 11:59PM on the day of disharge whether the patient will be readmitted within 30 days.

    Excludes:
        - Patients readmitted on same day as discharge
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        self.ontology: extension_datasets.Ontology = ontology
        self.time_horizon: TimeHorizon = TimeHorizon(
            start=datetime.timedelta(minutes=1), end=datetime.timedelta(days=30)
        )
        self.prediction_time_adjustment_func = move_datetime_to_end_of_day

    def get_outcome_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return the start times of inpatient admissions."""
        times: List[datetime.datetime] = []
        for admission_time, __ in get_inpatient_admission_discharge_times(patient, self.ontology):
            times.append(admission_time)
        return times

    def get_prediction_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return end of admission as prediction timm."""
        times: List[datetime.datetime] = []
        admission_times = set()
        for admission_time, discharge_time in get_inpatient_admission_discharge_times(patient, self.ontology):
            prediction_time: datetime.datetime = self.prediction_time_adjustment_func(discharge_time)
            # Ignore patients who are readmitted the same day they were discharged b/c of data leakage
            if prediction_time.replace(hour=0, minute=0, second=0, microsecond=0) in admission_times:
                continue
            times.append(prediction_time)
            admission_times.add(admission_time.replace(hour=0, minute=0, second=0, microsecond=0))
        times = sorted(list(set(times)))
        return times

    def get_time_horizon(self) -> TimeHorizon:
        return self.time_horizon


class Guo_ICUAdmissionLabeler(WithinVisitLabeler):
    """ICU admission prediction task from Guo et al. 2023.

    Binary prediction task @ 11:59PM on the day of admission
    whether the patient will be admitted to the ICU during their admission.

    Excludes:
        - Patients transfered on same day as admission
        - Visits where discharge occurs on the same day as admission
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        super().__init__(
            ontology=ontology,
            visit_start_adjust_func=move_datetime_to_end_of_day,
            visit_end_adjust_func=None,
        )

    def get_outcome_times(self, patient: Patient) -> List[datetime.datetime]:
        # Return the start times of all ICU admissions -- this is our outcome
        return [e.start for e in get_icu_events(patient, self.ontology)]  # type: ignore

    def get_visit_events(self, patient: Patient) -> List[Event]:
        """Return all inpatient visits where ICU transfer does not occur on the same day as admission."""
        # Get all inpatient visits -- each visit comprises a prediction (start, end) time horizon
        all_visits: List[Event] = get_inpatient_admission_events(patient, self.ontology)
        # Exclude visits where ICU admission occurs on the same day as admission
        icu_transfer_dates: List[datetime.datetime] = [
            x.replace(hour=0, minute=0, second=0, microsecond=0) for x in self.get_outcome_times(patient)
        ]
        valid_visits: List[Event] = []
        for visit in all_visits:
            # If admission and discharge are on the same day, then ignore
            if visit.start.date() == visit.end.date():
                continue
            # If ICU transfer occurs on the same day as admission, then ignore
            if visit.start.replace(hour=0, minute=0, second=0, microsecond=0) in icu_transfer_dates:
                continue
            valid_visits.append(visit)
        return valid_visits


##########################################################
##########################################################
# MIMIC-III Benchmark Tasks
# See: https://www.nature.com/articles/s41597-019-0103-9/figures/7 for
# details on how this was reproduced.
#
# Citation: Harutyunyan, H., Khachatrian, H., Kale, D.C. et al.
# Multitask learning and benchmarking with clinical time series data.
# Sci Data 6, 96 (2019). https://doi.org/10.1038/s41597-019-0103-9
##########################################################
##########################################################


class Harutyunyan_DecompensationLabeler(CodeLabeler):
    """Decompensation prediction task from Harutyunyan et al. 2019.

    Hourly binary prediction task on whether the patient dies in the next 24 hours.
    Make prediction every 60 minutes after ICU admission, starting at hour 4.

    Excludes:
        - ICU admissions with no length-of-stay (i.e. `event.end is None` )
        - ICU admissions < 4 hours
        - ICU admissions with no events
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        # Next 24 hours
        time_horizon = TimeHorizon(datetime.timedelta(hours=0), datetime.timedelta(hours=24))
        # Death events
        outcome_codes = list(get_femr_codes(ontology, get_death_concepts(), is_ontology_expansion=True))
        # Save ontology for `get_prediction_times()`
        self.ontology = ontology

        super().__init__(
            outcome_codes=outcome_codes,
            time_horizon=time_horizon,
        )

    def is_discard_censored_labels(self) -> bool:
        """Consider censored patients to be alive."""
        return False

    def get_prediction_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return a list of every hour after every ICU visit,
            up until death occurs or end of visit.
        Note that this requires creating an artificial event for
            each hour since there will only be one true
            event per ICU admission, but we'll need to create many
            subevents (at each hour) within this event.
        Also note that these events may not align with :00 minutes
            if the ICU visit does not start exactly "on the hour".

        Excludes:
            - ICU admissions with no length-of-stay (i.e. `event.end is None` )
            - ICU admissions < 4 hours
            - ICU admissions with no events
        """
        times: List[datetime.datetime] = []
        icu_events: List[Tuple[int, Event]] = get_icu_events(patient, self.ontology, is_return_idx=True)  # type: ignore
        icu_event_idxs = [idx for idx, __ in icu_events]
        death_times: List[datetime.datetime] = self.get_outcome_times(patient)
        earliest_death_time: datetime.datetime = min(death_times) if len(death_times) > 0 else datetime.datetime.max
        for __, e in icu_events:
            if (
                e.end is not None
                and e.end - e.start >= datetime.timedelta(hours=4)
                and does_exist_event_within_time_range(patient, e.start, e.end, exclude_event_idxs=icu_event_idxs)
            ):
                # Record every hour after admission (i.e. every hour between `e.start` and `e.end`),
                # but only after 4 hours have passed (i.e. start at `e.start + 4 hours`)
                # and only until the visit ends (`e.end`) or a death event occurs (`earliest_death_time`)
                end_of_stay: datetime.datetime = min(e.end, earliest_death_time)
                event_time = e.start + datetime.timedelta(hours=4)
                while event_time < end_of_stay:
                    times.append(event_time)
                    event_time += datetime.timedelta(hours=1)
        return times


class Harutyunyan_MortalityLabeler(WithinVisitLabeler):
    """In-hospital mortality prediction task from Harutyunyan et al. 2019.
    Single binary prediction task of whether patient dies within ICU admission 48 hours after admission.
    Make prediction 48 hours into ICU admission.

    Excludes:
        - ICU admissions with no length-of-stay (i.e. `event.end is None` )
        - ICU admissions < 48 hours
        - ICU admissions with no events before 48 hours
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        visit_start_adjust_func = lambda x: x + datetime.timedelta(  # noqa
            hours=48
        )  # Make prediction 48 hours into ICU admission
        visit_end_adjust_func = identity
        super().__init__(ontology, visit_start_adjust_func, visit_end_adjust_func)

    def is_discard_censored_labels(self) -> bool:
        """Consider censored patients to be alive."""
        return False

    def get_outcome_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return a list of all times when the patient experiences an outcome"""
        outcome_codes = list(get_femr_codes(self.ontology, get_death_concepts(), is_ontology_expansion=True))
        times: List[datetime.datetime] = []
        for e in patient.events:
            if e.code in outcome_codes:
                times.append(e.start)
        return times

    def get_visit_events(self, patient: Patient) -> List[Event]:
        """Return a list of all ICU visits > 48 hours.

        Excludes:
            - ICU admissions with no length-of-stay (i.e. `event.end is None` )
            - ICU admissions < 48 hours
            - ICU admissions with no events before 48 hours
        """
        icu_events: List[Tuple[int, Event]] = get_icu_events(patient, self.ontology, is_return_idx=True)  # type: ignore
        icu_event_idxs = [idx for idx, __ in icu_events]
        valid_events: List[Event] = []
        for __, e in icu_events:
            if (
                e.end is not None
                and e.end - e.start >= datetime.timedelta(hours=48)
                and does_exist_event_within_time_range(
                    patient, e.start, e.start + datetime.timedelta(hours=48), exclude_event_idxs=icu_event_idxs
                )
            ):
                valid_events.append(e)
        return valid_events


class Harutyunyan_LengthOfStayLabeler(Labeler):
    """LOS remaining regression task from Harutyunyan et al. 2019.

    Hourly regression task on the patient's remaining length-of-stay (in hours) in the ICU.
    Make prediction every 60 minutes after ICU admission, starting at hour 4.

    Excludes:
        - ICU admissions with no length-of-stay (i.e. `event.end is None` )
        - ICU admissions < 4 hours
        - ICU admissions with no events
    """

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        self.ontology = ontology

    def get_outcome_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return a list of all times when the patient experiences an outcome"""
        outcome_codes = list(get_femr_codes(self.ontology, get_death_concepts(), is_ontology_expansion=True))
        times: List[datetime.datetime] = []
        for e in patient.events:
            if e.code in outcome_codes:
                times.append(e.start)
        return times

    def get_labeler_type(self) -> LabelType:
        return "numeric"

    def label(self, patient: Patient) -> List[Label]:
        """Return a list of Labels at every hour after every ICU visit,
            where each Label is the # of hours
            until the visit ends (or a death event occurs).
        Note that this requires creating an artificial event for each
            hour since there will only be one true
        event per ICU admission, but we'll need to create many subevents
            (at each hour) within this event.
        Also note that these events may not align with :00 minutes if
            the ICU visit does not start exactly "on the hour".

        Excludes:
            - ICU admissions with no length-of-stay (i.e. `event.end is None` )
            - ICU admissions < 4 hours
            - ICU admissions with no events
        """
        labels: List[Label] = []
        icu_events: List[Tuple[int, Event]] = get_icu_events(patient, self.ontology, is_return_idx=True)  # type: ignore
        icu_event_idxs = [idx for idx, __ in icu_events]
        death_times: List[datetime.datetime] = self.get_outcome_times(patient)
        earliest_death_time: datetime.datetime = min(death_times) if len(death_times) > 0 else datetime.datetime.max
        for __, e in icu_events:
            if (
                e.end is not None
                and e.end - e.start >= datetime.timedelta(hours=4)
                and does_exist_event_within_time_range(patient, e.start, e.end, exclude_event_idxs=icu_event_idxs)
            ):
                # Record every hour after admission (i.e. every hour between
                #   `e.start` and `e.end`),
                # but only after 4 hours have passed (i.e. start at
                #   `e.start + 4 hours`)
                # and only until the visit ends (`e.end`) or a death event
                #   occurs (`earliest_death_time`)
                end_of_stay: datetime.datetime = min(e.end, earliest_death_time)
                event_time = e.start + datetime.timedelta(hours=4)
                while event_time < end_of_stay:
                    los: float = (end_of_stay - event_time).total_seconds() / 3600
                    labels.append(Label(event_time, los))
                    event_time += datetime.timedelta(hours=1)
                    assert los >= 0, (
                        f"LOS should never be negative, but end_of_stay={end_of_stay}"
                        f" - event_time={event_time} = {end_of_stay - event_time} for"
                        f" patient {patient.patient_id}"
                    )
        return labels


##########################################################
##########################################################
# Abnormal Lab Value Tasks
#
# Citation: Few shot EHR benchmark (ours)
##########################################################
##########################################################


class ThrombocytopeniaInstantLabValueLabeler(InstantLabValueLabeler):
    """lab-based definition for thrombocytopenia based on platelet count (10^9/L).
    Thresholds: mild (<150), moderate(<100), severe(<50), and reference range."""

    original_omop_concept_codes = [
        "LOINC/LP393218-5",
        "LOINC/LG32892-8",
        "LOINC/777-3",
    ]

    def value_to_label(self, raw_value: str, unit: Optional[str]) -> str:
        if raw_value.lower() in ["normal", "adequate"]:
            return "normal"
        value = float(raw_value)
        if value < 50:
            return "severe"
        elif value < 100:
            return "moderate"
        elif value < 150:
            return "mild"
        return "normal"


class HyperkalemiaInstantLabValueLabeler(InstantLabValueLabeler):
    """lab-based definition for hyperkalemia using blood potassium concentration (mmol/L).
    Thresholds: mild(>5.5),moderate(>6),severe(>7), and abnormal range."""

    original_omop_concept_codes = [
        "LOINC/LG7931-1",
        "LOINC/LP386618-5",
        "LOINC/LG10990-6",
        "LOINC/6298-4",
        "LOINC/2823-3",
    ]

    def value_to_label(self, raw_value: str, unit: Optional[str]) -> str:
        if raw_value.lower() in ["normal", "adequate"]:
            return "normal"
        value = float(raw_value)
        if unit is not None:
            unit = unit.lower()
            if unit.startswith("mmol/l"):
                # mmol/L
                # Original OMOP concept ID: 8753
                value = value
            elif unit.startswith("meq/l"):
                # mEq/L (1-to-1 -> mmol/L)
                # Original OMOP concept ID: 9557
                value = value
            elif unit.startswith("mg/dl"):
                # mg / dL (divide by 18 to get mmol/L)
                # Original OMOP concept ID: 8840
                value = value / 18.0
            else:
                raise ValueError(f"Unknown unit: {unit}")
        else:
            raise ValueError(f"Unknown unit: {unit}")
        if value > 7:
            return "severe"
        elif value > 6.0:
            return "moderate"
        elif value > 5.5:
            return "mild"
        return "normal"


class HypoglycemiaInstantLabValueLabeler(InstantLabValueLabeler):
    """lab-based definition for hypoglycemia using blood glucose concentration (mmol/L).
    Thresholds: mild(<3), moderate(<3.5), severe(<=3.9), and abnormal range."""

    original_omop_concept_codes = [
        "SNOMED/33747003",
        "LOINC/LP416145-3",
        "LOINC/14749-6",
    ]

    def value_to_label(self, raw_value: str, unit: Optional[str]) -> str:
        if raw_value.lower() in ["normal", "adequate"]:
            return "normal"
        value = float(raw_value)
        if unit is not None:
            unit = unit.lower()
            if unit.startswith("mg/dl"):
                # mg / dL
                # Original OMOP concept ID: 8840, 9028
                value = value / 18
            elif unit.startswith("mmol/l"):
                # mmol / L (x 18 to get mg/dl)
                # Original OMOP concept ID: 8753
                value = value
            else:
                raise ValueError(f"Unknown unit: {unit}")
        else:
            raise ValueError(f"Unknown unit: {unit}")
        if value < 3:
            return "severe"
        elif value < 3.5:
            return "moderate"
        elif value <= 3.9:
            return "mild"
        return "normal"


class HyponatremiaInstantLabValueLabeler(InstantLabValueLabeler):
    """lab-based definition for hyponatremia based on blood sodium concentration (mmol/L).
    Thresholds: mild (<=135),moderate(<130),severe(<125), and abnormal range."""

    original_omop_concept_codes = ["LOINC/LG11363-5", "LOINC/2951-2", "LOINC/2947-0"]

    def value_to_label(self, raw_value: str, unit: Optional[str]) -> str:
        if raw_value.lower() in ["normal", "adequate"]:
            return "normal"
        value = float(raw_value)
        if value < 125:
            return "severe"
        elif value < 130:
            return "moderate"
        elif value <= 135:
            return "mild"
        return "normal"


class AnemiaInstantLabValueLabeler(InstantLabValueLabeler):
    """lab-based definition for anemia based on hemoglobin levels (g/L).
    Thresholds: mild(<120),moderate(<110),severe(<70), and reference range"""

    original_omop_concept_codes = [
        "LOINC/LP392452-1",
    ]

    def value_to_label(self, raw_value: str, unit: Optional[str]) -> str:
        if raw_value.lower() in ["normal", "adequate"]:
            return "normal"
        value = float(raw_value)
        if unit is not None:
            unit = unit.lower()
            if unit.startswith("g/dl"):
                # g / dL
                # Original OMOP concept ID: 8713
                # NOTE: This weird *10 / 100 is how Lawrence did it
                value = value * 10
            elif unit.startswith("mg/dl"):
                # mg / dL (divide by 1000 to get g/dL)
                # Original OMOP concept ID: 8840
                # NOTE: This weird *10 / 100 is how Lawrence did it
                value = value / 100
            elif unit.startswith("g/l"):
                value = value
            else:
                raise ValueError(f"Unknown unit: {unit}")
        else:
            raise ValueError(f"Unknown unit: {unit}")
        if value < 70:
            return "severe"
        elif value < 110:
            return "moderate"
        elif value < 120:
            return "mild"
        return "normal"


##########################################################
##########################################################
# First Diagnosis Tasks
# See: https://github.com/som-shahlab/few_shot_ehr/tree/main
#
# Citation: Few shot EHR benchmark (ours)
##########################################################
##########################################################


class FirstDiagnosisTimeHorizonCodeLabeler(TimeHorizonEventLabeler):
    """Predict if patient will have their *first* diagnosis of `self.root_concept_code` in the next (1, 365) days.

    Make prediction at 11:59pm on day of discharge from inpatient admission.

    Excludes:
        - Patients who have already had this diagnosis
    """

    root_concept_code: Optional[str] = None  # OMOP concept code for outcome, e.g. "SNOMED/57054005"

    def __init__(
        self,
        ontology: extension_datasets.Ontology,
    ):
        assert (
            self.root_concept_code is not None
        ), "Must specify `root_concept_code` for `FirstDiagnosisTimeHorizonCodeLabeler`"
        self.ontology = ontology
        self.outcome_codes = list(get_femr_codes(ontology, [self.root_concept_code], is_ontology_expansion=True))
        self.time_horizon: TimeHorizon = TimeHorizon(datetime.timedelta(minutes=1), datetime.timedelta(days=365))

    def get_prediction_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return discharges that occur before first diagnosis of outcome as prediction times."""
        times: List[datetime.datetime] = []
        for __, discharge_time in get_inpatient_admission_discharge_times(patient, self.ontology):
            prediction_time: datetime.datetime = move_datetime_to_end_of_day(discharge_time)
            times.append(prediction_time)
        times = sorted(list(set(times)))

        # Drop all times that occur after first diagnosis
        valid_times: List[datetime.datetime] = []
        outcome_times: List[datetime.datetime] = self.get_outcome_times(patient)
        if len(outcome_times) == 0:
            return times
        else:
            first_diagnosis_time: datetime.datetime = min(outcome_times)
            for t in times:
                if t < first_diagnosis_time:
                    valid_times.append(t)
            return valid_times

    def get_outcome_times(self, patient: Patient) -> List[datetime.datetime]:
        """Return the start times of this patient's events whose `code` is in `self.outcome_codes`."""
        times: List[datetime.datetime] = []
        for event in patient.events:
            if event.code in self.outcome_codes:
                times.append(event.start)
        return times

    def get_time_horizon(self) -> TimeHorizon:
        return self.time_horizon

    def is_discard_censored_labels(self) -> bool:
        return True

    def allow_same_time_labels(self) -> bool:
        return False


class PancreaticCancerCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 200684
    root_concept_code = "SNOMED/372003004"


class CeliacDiseaseCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 60270
    root_concept_code = "SNOMED/396331005"


class LupusCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 176684
    root_concept_code = "SNOMED/55464009"


class AcuteMyocardialInfarctionCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 21982
    root_concept_code = "SNOMED/57054005"


class CTEPHCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 1433
    root_concept_code = "SNOMED/233947005"


class EssentialHypertensionCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 4644483
    root_concept_code = "SNOMED/59621000"


class HyperlipidemiaCodeLabeler(FirstDiagnosisTimeHorizonCodeLabeler):
    # n = 3048320
    root_concept_code = "SNOMED/55822004"

##########################################################
##########################################################
# CheXpert
##########################################################
##########################################################

CHEXPERT_LABELS = [
    "No Finding",
    "Enlarged Cardiomediastinum",
    "Cardiomegaly",
    "Lung Lesion",
    "Lung Opacity",
    "Edema",
    "Consolidation",
    "Pneumonia",
    "Atelectasis",
    "Pneumothorax",
    "Pleural Effusion",
    "Pleural Other",
    "Fracture",
    "Support Devices",
]


class ChexpertLabeler(Labeler):
    """CheXpert labeler.

    Multi-label classification task of patient's radiology reports.
    Make prediction 24 hours before radiology note is recorded.

    Excludes:
        - Radiology reports that are written <=24 hours of a patient's first event (i.e. `patient.events[0].start`)
    """

    def __init__(
        self,
        path_to_chexpert_csv: str,
    ):
        self.path_to_chexpert_csv = path_to_chexpert_csv
        self.prediction_offset: datetime.timedelta = datetime.timedelta(hours=-24)
        self.df_chexpert = pd.read_csv(self.path_to_chexpert_csv, sep="\t").sort_values(by=["start"], ascending=True)

    def get_labeler_type(self) -> LabelType:
        return "categorical"

    def label(self, patient: Patient) -> List[Label]:  # type: ignore
        labels: List[Label] = []
        patient_start_time, _ = self.get_patient_start_end_times(patient)
        df_patient = self.df_chexpert[self.df_chexpert["patient_id"] == patient.patient_id].sort_values(
            by=["start"], ascending=True
        )

        for idx, row in df_patient.iterrows():
            label_time: datetime.datetime = datetime.datetime.fromisoformat(row["start"])
            prediction_time: datetime.datetime = label_time + self.prediction_offset
            if prediction_time <= patient_start_time:
                # Exclude radiology reports where our prediction time would be before patient's first timeline event
                continue

            bool_labels = row[CHEXPERT_LABELS].astype(int).to_list()
            label_string = "".join([str(x) for x in bool_labels])
            label_num: int = int(label_string, 2)
            labels.append(Label(time=prediction_time, value=label_num))

        return labels


if __name__ == "__main__":
    pass
