"""Alert CRUD and triggered-alert log endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.db import AlertRule
from backend.app.models.schemas import AlertCreate, AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _to_out(a: AlertRule) -> AlertOut:
    return AlertOut(
        id=a.id,
        ticker=a.ticker,
        alert_type=a.alert_type,
        threshold=a.threshold,
        notification_channels=a.notification_channels.split(","),
        is_active=a.is_active,
        triggered_at=a.triggered_at,
        created_at=a.created_at,
    )


@router.get("/", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db)) -> list[AlertOut]:
    return [_to_out(a) for a in db.query(AlertRule).order_by(AlertRule.created_at.desc()).all()]


@router.post("/", response_model=AlertOut, status_code=201)
def create_alert(payload: AlertCreate, db: Session = Depends(get_db)) -> AlertOut:
    alert = AlertRule(
        ticker=payload.ticker.upper(),
        alert_type=payload.alert_type,
        threshold=payload.threshold,
        notification_channels=",".join(payload.notification_channels),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _to_out(alert)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: int, db: Session = Depends(get_db)) -> None:
    alert = db.query(AlertRule).filter(AlertRule.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()


@router.patch("/{alert_id}/toggle", response_model=AlertOut)
def toggle_alert(alert_id: int, db: Session = Depends(get_db)) -> AlertOut:
    alert = db.query(AlertRule).filter(AlertRule.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_active = not alert.is_active
    db.commit()
    db.refresh(alert)
    return _to_out(alert)
