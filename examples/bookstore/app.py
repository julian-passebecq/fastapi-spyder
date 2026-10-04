"""Small app used to demonstrate contract/dependency lineage."""

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="Bookstore Data API", version="0.1")


class BookMetadata(BaseModel):
    isbn: str = Field(description="13 digit ISBN")
    title: str
    author: str

    @field_validator("isbn")
    @classmethod
    def isbn_must_be_13_digits(cls, value: str) -> str:
        if not value.isdigit() or len(value) != 13:
            raise ValueError("ISBN must be exactly 13 digits")
        return value


class StoredBook(BaseModel):
    isbn: str
    status: str


def get_request_context(ingestion_id: str = Header()) -> str:
    return ingestion_id


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/books/metadata", response_model=StoredBook, status_code=201)
def ingest_metadata(
    book: BookMetadata,
    ingestion_id: str = Depends(get_request_context),
) -> StoredBook:
    _ = ingestion_id
    return StoredBook(isbn=book.isbn, status="stored")
