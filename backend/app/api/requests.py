"""Reading the requests captured by an endpoint."""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import EndpointDep, SessionDep
from app.models import CapturedRequest, SignatureCheck
from app.schemas.request import RequestDetail, RequestPage, RequestSummary

router = APIRouter(prefix="/api/endpoints/{view_token}/requests", tags=["requests"])

MAX_PAGE_SIZE = 100


@router.get("", response_model=RequestPage)
async def list_requests(
    endpoint: EndpointDep,
    session: SessionDep,
    before: int | None = Query(
        default=None,
        description="Return captures older than this id. Omit for the newest page.",
    ),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
) -> RequestPage:
    """List captures, newest first, paginated by cursor."""
    query = (
        # An outer join rather than a relationship: most captures have no check,
        # and an ORM relationship would either lazily fire one query per row from
        # inside the serialiser -- which async SQLAlchemy cannot do -- or need
        # eager-loading config that is easy to forget on a new query.
        select(CapturedRequest, SignatureCheck)
        .outerjoin(SignatureCheck, SignatureCheck.request_id == CapturedRequest.id)
        .where(CapturedRequest.endpoint_id == endpoint.id)
        .order_by(CapturedRequest.id.desc())
        # One extra row is fetched to find out whether another page exists,
        # without a second COUNT query over the whole table.
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(CapturedRequest.id < before)

    rows = list((await session.execute(query)).all())

    has_more = len(rows) > limit
    page = rows[:limit]

    return RequestPage(
        items=[RequestSummary.from_model(captured, check) for captured, check in page],
        next_cursor=page[-1][0].id if has_more and page else None,
    )


async def _load(
    session: SessionDep, endpoint: EndpointDep, request_id: int
) -> tuple[CapturedRequest, SignatureCheck | None]:
    """Fetch one capture and its verdict, scoped to the endpoint the token unlocked.

    The endpoint_id filter is what stops a caller from reading someone else's
    capture by guessing a sequential id -- which is trivial, since ids are
    consecutive integers by design.
    """
    result = await session.execute(
        select(CapturedRequest, SignatureCheck)
        .outerjoin(SignatureCheck, SignatureCheck.request_id == CapturedRequest.id)
        .where(
            CapturedRequest.id == request_id,
            CapturedRequest.endpoint_id == endpoint.id,
        )
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    return row[0], row[1]


@router.get("/{request_id}", response_model=RequestDetail)
async def get_request(
    request_id: int,
    endpoint: EndpointDep,
    session: SessionDep,
) -> RequestDetail:
    """One capture, with headers, query, body and signature verdict."""
    captured, check = await _load(session, endpoint, request_id)
    return RequestDetail.from_model(captured, check)


@router.get("/{request_id}/body")
async def download_body(
    request_id: int,
    endpoint: EndpointDep,
    session: SessionDep,
) -> Response:
    """Download the raw body, byte for byte.

    It is deliberately NOT served with the content type it arrived as. Echoing
    back `text/html` -- or anything else a stranger chose -- would let this
    domain serve attacker-controlled content that the browser renders, turning it
    into free malware hosting and burning the domain's reputation, which is very
    hard to get back.

    So: always octet-stream, always an attachment, plus `nosniff` to stop the
    browser second-guessing the type from the bytes.
    """
    captured, _ = await _load(session, endpoint, request_id)

    return Response(
        content=captured.body_raw or b"",
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="request-{captured.id}.bin"',
            "X-Content-Type-Options": "nosniff",
        },
    )
