from datetime import datetime, timezone


async def run_ingest(source: str) -> dict:
    """Safe integration point for future provider jobs.

    Keep provider-specific parsing in backend/providers; this function owns the
    logging and database write lifecycle once Supabase ingestion is switched on.
    """
    return {
        "source": source,
        "status": "accepted",
        "message": "Ingestion contract is ready; connect this source adapter and Supabase write in the next increment.",
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
