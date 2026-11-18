"""Train a 10-minute soil moisture forecast from prepared train/test CSVs."""

import argparse
from pathlib import Path
import matplotlib.pyplot as plt
import joblib
import pandas as pd
import field_constants as fc
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "datasets"

BASE_FEATURES = [
    fc.SOIL_MOISTURE,
    fc.WEATHER_TEMPERATURE,
    fc.WEATHER_HUMIDITY,
    fc.IRRIGATION_PAST_10M,
    fc.IRRIGATION_DURATION_MINUTES,
]
DELTA_FEATURE = fc.SOIL_CHANGE_10M
TARGET = "y"


def save_prediction_scatter(test, predictions, output_path):
    actual = test[TARGET].to_numpy()
    irrigated = test[fc.IRRIGATION_DURATION_MINUTES].gt(0).to_numpy()

    fig, ax = plt.subplots(figsize=(7, 7), layout="constrained")

    ax.scatter(
        actual[~irrigated],
        predictions[~irrigated],
        s=9,
        alpha=0.15,
        color="tab:blue",
        label="Without irrigation",
    )
    ax.scatter(
        actual[irrigated],
        predictions[irrigated],
        s=24,
        alpha=0.8,
        color="tab:orange",
        label="With irrigation",
    )

    ax.plot([0, 100], [0, 100], "--", color="black", label="y = x")
    ax.set(
        xlim=(0, 100),
        ylim=(0, 100),
        xlabel="Actual soil moisture after 10 min (%)",
        ylabel="Predicted soil moisture after 10 min (%)",
        title="10-minute soil moisture forecast",
    )
    ax.set_aspect("equal", adjustable="box")
    ax.legend()

    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def load_data(path: Path, features: list[str]) -> pd.DataFrame:
    data = pd.read_csv(path)

    required_columns = [*features, TARGET]
    missing_columns = [
        column for column in required_columns if column not in data.columns
    ]
    if missing_columns:
        raise ValueError(f"{path.name}: missing columns {', '.join(missing_columns)}")

    if data.empty:
        raise ValueError(f"{path.name}: no rows")

    # Only the moisture delta is allowed to be missing.
    complete_columns = [
        column for column in required_columns if column != DELTA_FEATURE
    ]
    columns_with_nulls = [
        column for column in complete_columns if data[column].isna().any()
    ]
    if columns_with_nulls:
        raise ValueError(
            f"{path.name}: unexpected nulls in {', '.join(columns_with_nulls)}"
        )

    return data


def report_test_results(test: pd.DataFrame, predictions) -> None:
    test = test.assign(prediction=predictions)
    irrigated = test[fc.IRRIGATION_DURATION_MINUTES].gt(0)

    groups = [
        ("All test rows", test),
        ("Without irrigation", test.loc[~irrigated]),
        ("With irrigation", test.loc[irrigated]),
    ]

    for name, rows in groups:
        if rows.empty:
            print(f"{name}: no rows")
            continue

        mae = mean_absolute_error(rows[TARGET], rows["prediction"])
        persistence_mae = mean_absolute_error(
            rows[TARGET], rows[fc.SOIL_MOISTURE]
        )
        print(
            f"{name} ({len(rows):,} rows): "
            f"RF MAE {mae:.3f} pp | "
            f"persistence MAE {persistence_mae:.3f} pp"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, default=DATA_DIR / "train_data.csv")
    parser.add_argument("--test", type=Path, default=DATA_DIR / "test_data.csv")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=-1)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument(
        "--without-delta",
        action="store_true",
        help="Train a comparison model without soil_delta_10m.",
    )
    args = parser.parse_args()

    features = BASE_FEATURES.copy()
    if not args.without_delta:
        features.append(DELTA_FEATURE)

    train = load_data(args.train, features)
    test = load_data(args.test, features)

    model = Pipeline([
        (
            "imputer",
            SimpleImputer(strategy="constant", fill_value=0, add_indicator=True),
        ),
        (
            "forest",
            RandomForestRegressor(
                n_estimators=200,
                max_depth=10,
                min_samples_leaf=10,
                random_state=42,
                n_jobs=args.jobs,
            ),
        ),
    ])

    print(f"Train: {len(train):,} rows | Test: {len(test):,} rows")
    print(f"Features: {', '.join(features)}")

    model.fit(train[features], train[TARGET])
    predictions = model.predict(test[features])
    report_test_results(test, predictions)

    default_name = (
        "irrigation_rf_10m_without_delta.joblib"
        if args.without_delta
        else "irrigation_rf_10m_with_delta.joblib"
    )
    
    output = args.output or SCRIPT_DIR / default_name
    joblib.dump(model, output)
    print(f"Saved model: {output}")
    
    if args.no_plot:
        return

    plot_path = output.with_name(f"{output.stem}_scatter.png")
    save_prediction_scatter(test, predictions, plot_path)
    print(f"Saved scatter plot: {plot_path}")


if __name__ == "__main__":
    main()