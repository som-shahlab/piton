"""Labeling functions for OMOP data based on lab values."""
from __future__ import annotations

import datetime
from abc import abstractmethod
from typing import List, Optional, Set

import meds
import femr.ontology

from .core import (
    Labeler,
    OMOPConceptCodeLabeler, 
)

##########################################################
##########################################################
# Labelers based on Lab Values.
#
# The difference between these Labelers and the ones based on codes
# is that these Labelers are based on lab values, not coded
# diagnoses. Thus, they may catch more cases of a given
# condition due to under-coding, but they are also more
# likely to be noisy.
##########################################################
##########################################################


class InstantLabValueLabeler(Labeler):
    """Apply a multi-class label for the outcome of a lab test.

    Prediction Time: Immediately before lab result is returned (i.e. 1 minute before)
    Time Horizon: The next immediate result for this lab test
    Label: Severity level of lab

    Excludes:
        - Labels that occur at the same exact time as the very first event in a patient's history
    """

    # parent OMOP concept codes, from which all the outcomes are derived (as children in our ontology)
    original_omop_concept_codes: List[str] = []

    def __init__(
        self,
        ontology: femr.ontology.Ontology,
    ):
        self.ontology: femr.ontology.Ontology = ontology
        self.outcome_codes: Set[str] = ontology.get_all_children(self.original_omop_concept_codes)

    def label(self, patient: meds.Patient, is_show_warnings: bool = False) -> List[meds.Label]:
        labels: List[meds.Label] = []
        for e in patient["events"]:
            if patient["events"][0]["time"] == e["time"]:
                # Ignore events that occur at the same time as the first event in the patient's history
                continue
            for m in e["measurements"]:
                if m["code"] in self.outcome_codes:
                    # This is an outcome event
                    if m['numeric_value'] is not None:
                        try:
                            # `e.unit` is string of form "mg/dL", "ounces", etc.
                            label: int = self.label_to_int(self.value_to_label(m['numeric_value'], m['metadata']['unit']))
                            prediction_time: datetime.datetime = e["time"] - datetime.timedelta(minutes=1)
                            labels.append(meds.Label(patient_id=patient["patient_id"], prediction_time=prediction_time, integer_value=label))
                        except Exception as exception:
                            if is_show_warnings:
                                print(
                                    f"Warning: Error parsing value='{m['numeric_value']}' with metadata='{m['metadata']}'"
                                    f" for code='{m['code']}' @ {e['time']} for patient_id='{patient['patient_id']}'"
                                    f" | Exception: {exception}"
                                )
        return labels

    def label_to_int(self, label: str) -> int:
        if label == "normal":
            return 0
        elif label == "mild":
            return 1
        elif label == "moderate":
            return 2
        elif label == "severe":
            return 3
        raise ValueError(f"Invalid label without a corresponding int: {label}")

    @abstractmethod
    def value_to_label(self, raw_value: float, unit: Optional[str]) -> str:
        """Convert `value` to a string label: "mild", "moderate", "severe", or "normal".
        NOTE: Some units have the form 'mg/dL (See scan or EMR data for detail)', so you
        need to use `.startswith()` to check for the unit you want.
        """
        return "normal"


##########################################################
##########################################################
# Labeling functions derived from OMOPConceptCodeLabeler
##########################################################
##########################################################


class HypoglycemiaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Hypoglycemia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/267384006', 'SNOMED/421725003', 'SNOMED/719216001',
        'SNOMED/302866003', 'SNOMED/237633009', 'SNOMED/120731000119103',
        'SNOMED/190448007', 'SNOMED/230796005', 'SNOMED/421437000',
        'SNOMED/52767006', 'SNOMED/237637005', 'SNOMED/84371000119108'
    ]
    # fmt: on


class AKICodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of AKI in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/14669001', 'SNOMED/298015003', 'SNOMED/35455006',
    ]
    # fmt: on


class AnemiaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Anemia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/271737000', 'SNOMED/713496008', 'SNOMED/713349004', 'SNOMED/767657005',
        'SNOMED/111570005', 'SNOMED/691401000119104', 'SNOMED/691411000119101',
    ]
    # fmt: on


class HyperkalemiaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Hyperkalemia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/14140009',
    ]
    # fmt: on


class HyponatremiaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Hyponatremia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/267447008', 'SNOMED/89627008'
    ]
    # fmt: on


class ThrombocytopeniaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Thrombocytopenia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/267447008', 'SNOMED/89627008',
    ]
    # fmt: on


class NeutropeniaCodeLabeler(OMOPConceptCodeLabeler):
    """Apply a label for whether a patient has at 1+ explicitly
    coded occurrence(s) of Neutkropenia in `time_horizon`."""

    # fmt: off
    original_omop_concept_codes = [
        'SNOMED/165517008',
    ]
    # fmt: on