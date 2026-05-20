from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import ConferenceOut, EventOut, SuggestionOut
from app.services.catalog import CatalogRepo, get_catalog_repo
from app.services.suggestions_repo import SuggestionsRepo, get_suggestions_repo

router = APIRouter(prefix="/api/conferences", tags=["conferences"])

VALID_KINDS = {"people", "companies", "speakers"}


@router.get("", response_model=list[ConferenceOut])
def list_conferences(
    repo: Annotated[CatalogRepo, Depends(get_catalog_repo)],
) -> list[ConferenceOut]:
    return repo.list_conferences()


@router.get("/{conference_id}", response_model=ConferenceOut)
def get_conference(
    conference_id: str,
    repo: Annotated[CatalogRepo, Depends(get_catalog_repo)],
) -> ConferenceOut:
    conf = repo.get_conference(conference_id)
    if conf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conference not found")
    return conf


@router.get("/{conference_id}/events", response_model=list[EventOut])
def list_conference_events(
    conference_id: str,
    repo: Annotated[CatalogRepo, Depends(get_catalog_repo)],
) -> list[EventOut]:
    # Validate conference exists for a clean 404 — keep the contract honest.
    if repo.get_conference(conference_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conference not found")
    return repo.list_events(conference_id)


@router.get("/{conference_id}/suggestions", response_model=list[SuggestionOut])
def list_conference_suggestions(
    conference_id: str,
    repo: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    kind: str | None = None,
    include_luma: bool = False,
) -> list[SuggestionOut]:
    """People / companies / speakers curated for this conference. Used by the
    onboarding "mustHaves" step to populate the picker. `kind` is the filter
    the UI is showing right now (`people`, `companies`, `speakers`).

    By default raw Luma-scraped rows are excluded — they're useful as input
    to the LLM (the generate-people endpoint reads them as `known_people`) but
    too noisy for the public picker (handles like "Berko", "Cocktail", etc.).
    Pass `include_luma=true` for admin debugging / inspection."""
    if repo.get_conference(conference_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conference not found")
    if kind is not None and kind not in VALID_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kind must be one of {sorted(VALID_KINDS)}",
        )
    rows = suggestions_repo.list_for_conference(conference_id)
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    if not include_luma:
        rows = [r for r in rows if r.get("source") != "luma"]
    return [SuggestionOut.model_validate(r) for r in rows]
