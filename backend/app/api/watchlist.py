"""Watchlist CRUD endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.db import WatchlistItem
from backend.app.models.schemas import WatchlistItemCreate, WatchlistItemOut

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("/", response_model=list[WatchlistItemOut])
def list_watchlist(db: Session = Depends(get_db)) -> list[WatchlistItemOut]:
    items = db.query(WatchlistItem).order_by(WatchlistItem.added_at.desc()).all()
    return [
        WatchlistItemOut(
            id=i.id,
            ticker=i.ticker,
            name=i.name,
            asset_type=i.asset_type,
            added_at=i.added_at,
            price=i.price,
            change_1d_pct=i.change_1d_pct,
            pulse_score=i.pulse_score,
        )
        for i in items
    ]


@router.post("/", response_model=WatchlistItemOut, status_code=201)
def add_to_watchlist(
    payload: WatchlistItemCreate, db: Session = Depends(get_db)
) -> WatchlistItemOut:
    existing = db.query(WatchlistItem).filter(WatchlistItem.ticker == payload.ticker.upper()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"'{payload.ticker}' is already in your watchlist")

    item = WatchlistItem(
        ticker=payload.ticker.upper(),
        name=payload.name,
        asset_type=payload.asset_type,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return WatchlistItemOut(
        id=item.id,
        ticker=item.ticker,
        name=item.name,
        asset_type=item.asset_type,
        added_at=item.added_at,
    )


@router.delete("/{ticker}", status_code=204)
def remove_from_watchlist(ticker: str, db: Session = Depends(get_db)) -> None:
    item = db.query(WatchlistItem).filter(WatchlistItem.ticker == ticker.upper()).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"'{ticker}' not in watchlist")
    db.delete(item)
    db.commit()


@router.post("/refresh", status_code=202, tags=["watchlist"])
def trigger_refresh() -> dict:
    """
    Manually trigger a price + pulse score refresh for all watchlist items.
    Runs in the background — returns immediately, refresh takes ~30-60 seconds.
    """
    import threading

    from backend.app.scheduler import _refresh_watchlist
    thread = threading.Thread(target=_refresh_watchlist, daemon=True, name="manual-refresh")
    thread.start()
    return {"status": "refresh started", "message": "Prices and scores will update in ~30-60 seconds"}
