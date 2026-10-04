import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

MODEL_DIR = Path(__file__).resolve().parent
MODEL_PATH = MODEL_DIR / "workout_model.joblib"

NUMERIC_FEATURES = ["weight_kg", "reps", "sets", "volume", "intensity"]
CATEGORICAL_FEATURES = ["category", "exercise"]


class WorkoutDifficultyPredictor:
    """Tabulär regressionsmodell (TabFM / Gradient Boosting) för prediktion

    av användarens upplevda ansträngningsgrad (difficulty/RPE, 1-10).
    """

    def __init__(self, model_path: Path | str = MODEL_PATH):
        self.model_path = Path(model_path)
        self.pipeline: Pipeline | None = None
        self._load_if_exists()

    def _load_if_exists(self) -> None:
        """Laddar en tidigare sparad modell från disk om den existerar."""
        if self.model_path.exists():
            try:
                loaded = joblib.load(self.model_path)
                if isinstance(loaded, Pipeline):
                    self.pipeline = loaded
            except Exception:
                self.pipeline = None

    def _prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Säkerställer att alla nödvändiga numeriska och kategoriska kolumner

        finns och har korrekta datatyper.
        """
        data = df.copy()

        # Numeriska kolumner
        for col in NUMERIC_FEATURES:
            if col not in data.columns:
                data[col] = 0.0
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)

        # Beräkna volym om den är 0
        zero_vol = data["volume"] <= 0
        if zero_vol.any():
            data.loc[zero_vol, "volume"] = (
                data.loc[zero_vol, "weight_kg"]
                * data.loc[zero_vol, "reps"]
                * data.loc[zero_vol, "sets"]
            )

        # Kategoriska kolumner
        for col in CATEGORICAL_FEATURES:
            if col not in data.columns:
                data[col] = "Unknown"
            data[col] = data[col].fillna("Unknown").astype(str)

        return data[NUMERIC_FEATURES + CATEGORICAL_FEATURES]

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        """Tränar modellen på slutförda pass där difficulty är ifylld.

        Beräknar MAE, RMSE och R^2 och sparar modellen i minnet och på disk.
        """
        if df.empty:
            raise ValueError("Ingen träningsdata skickades till modellen.")

        # Filtrera: enbart slutförda pass med giltigt difficulty-värde
        mask = (df["completed"] == True) & df["difficulty"].notna()  # noqa: E712
        train_df = df[mask].copy()

        if len(train_df) < 5:
            raise ValueError(
                f"För få träningsrader ({len(train_df)}) för att träna modellen."
            )

        X = self._prepare_features(train_df)
        y = pd.to_numeric(train_df["difficulty"], errors="coerce").astype(float)

        valid_idx = ~y.isna()
        X = X[valid_idx]
        y = y[valid_idx]

        # Förbered förbehandling med OneHotEncoding för kategorier
        preprocessor = ColumnTransformer(
            transformers=[
                ("num", "passthrough", NUMERIC_FEATURES),
                (
                    "cat",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    CATEGORICAL_FEATURES,
                ),
            ]
        )

        # Tabulär regressor optimerad för kontinuerlig MAE-minimering (L1 loss)
        regressor = HistGradientBoostingRegressor(
            loss="absolute_error",
            max_iter=300,
            learning_rate=0.05,
            min_samples_leaf=5,
            random_state=42,
        )

        pipeline = Pipeline([
            ("preprocessor", preprocessor),
            ("regressor", regressor),
        ])

        pipeline.fit(X, y)
        self.pipeline = pipeline

        # Utvärdering på träningsdata
        raw_preds = pipeline.predict(X)
        clipped_preds = np.clip(raw_preds, 1.0, 10.0)

        mae = float(mean_absolute_error(y, clipped_preds))
        rmse = float(root_mean_squared_error(y, clipped_preds))
        r2 = float(r2_score(y, clipped_preds))

        # Serialisera till disk som fallback
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(pipeline, self.model_path)
        except Exception:
            pass

        return {
            "status": "success",
            "trained_samples": int(len(train_df)),
            "mae": round(mae, 3),
            "rmse": round(rmse, 3),
            "r2": round(r2, 3),
        }

    def predict(self, df: pd.DataFrame) -> NDArray[np.float64]:
        """Förutspår svårighetsgrad (difficulty) för givna pass.

        Klipper värdena logiskt mellan 1.0 och 10.0 och avrundar till 1 decimal.
        """
        if self.pipeline is None:
            raise RuntimeError(
                "Modellen är inte tränad ännu. Kör train() först."
            )

        if df.empty:
            return np.array([], dtype=np.float64)

        X = self._prepare_features(df)
        raw_preds = self.pipeline.predict(X)
        clipped = np.clip(raw_preds, 1.0, 10.0)
        return np.round(clipped, 1).astype(np.float64)
