"""Rejection-risk model: predicts biopsy-proven rejection in the 90 days after a surveillance visit.

Trained on synthetic data, not clinically validated, decision support only.
"""

import os

# MLflow prints a hint for coding agents on import; it is noise in our logs.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

LABEL_TEXT = "Trained on synthetic data, not clinically validated, decision support only."
