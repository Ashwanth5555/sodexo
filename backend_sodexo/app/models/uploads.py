from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UploadedDataset(Base):
    """A user-uploaded file registered as a forecastable source.

    The rows themselves live on disk as CSV (stored_filename) rather than in a
    table: an upload has an arbitrary, unknown-in-advance schema, so there is no
    canonical table to put it in, and the forecasting engine wants a DataFrame
    anyway. This row holds only what we need to find the file again and to know
    how the user mapped its columns onto a time series.

    status: 'uploaded' once the file is parsed and profiled, 'configured' once
    the user has chosen the date/target columns and it can be trained.
    """

    __tablename__ = "uploaded_dataset"

    dataset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(260), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")

    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Full column profile from the parsed file: name, dtype, null %, samples.
    column_profile: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- the user's mapping of this file onto a time series -----------------
    date_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    group_column: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Pandas offset alias: H, D, W, MS. Drives resampling and seasonality.
    frequency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # How duplicate rows in the same period are combined: sum or mean.
    aggregation: Mapped[str] = mapped_column(String(10), default="sum", nullable=False)
    horizon_periods: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    # Numeric columns to feed the models as exogenous drivers, lagged so they
    # are always already-observed at forecast time.
    exog_columns: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    configured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
