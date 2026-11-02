import json
from pathlib import Path
from uuid import UUID

import pytest

from vitrina_catalog.importer import (
    PRICE_MAX,
    DraftProduct,
    extract_products,
    parse_products,
    photo_type,
    save_drafts,
)
from vitrina_catalog.memory import InMemoryCatalog
from vitrina_core.jobs import JobFailedError
from vitrina_core.providers import (
    Blob,
    CallMeta,
    CassetteMode,
    Cassettes,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    RecordedChatModel,
    TextPart,
)

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
MENU = Blob(data=b"\xff\xd8\xff synthetic menu photo", mime_type="image/jpeg")
MODEL = "model-from-settings"


class FakeSpark:
    """Answers each call with the next text; records the requests."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self.answers.pop(0)),
            finish_reason="stop",
            meta=CallMeta(provider="meta_model_api", model=request.model, purpose=request.purpose),
        )


def products(*items: dict[str, object]) -> str:
    return json.dumps({"products": list(items)}, ensure_ascii=False)


BANDEJA = {"name": "Bandeja paisa", "price_cop": 25000, "description": "Con chicharrón"}
LIMONADA = {"name": "Limonada de coco", "price_cop": 9000, "description": ""}


async def test_as_1_3_spark_reads_the_photo_once_with_the_schema_and_the_hint() -> None:
    spark = FakeSpark(products(BANDEJA))

    await extract_products(MENU, "menú de almuerzos", chat=spark, model=MODEL)

    [request] = spark.requests
    assert (request.model, request.purpose, request.temperature) == (MODEL, "agent", 0.0)
    assert request.response_format is not None
    assert request.response_format.name == "menu_products"
    system, user = request.messages
    assert system.role == "system"
    assert isinstance(user.content, tuple)
    assert ImagePart(image=MENU) in user.content
    assert TextPart(text="Contexto del vendedor: menú de almuerzos") in user.content


async def test_as_1_3_the_answer_becomes_drafts_with_whole_peso_prices() -> None:
    spark = FakeSpark(
        products(
            BANDEJA,
            {"name": "  Limonada   de coco ", "price_cop": "$9.000", "description": ""},
            {"name": "Jugo de mora", "price_cop": "7 mil", "description": "En agua o leche"},
        )
    )

    drafts = await extract_products(MENU, None, chat=spark, model=MODEL)

    assert drafts == [
        DraftProduct(name="Bandeja paisa", price_cop=25000, description="Con chicharrón"),
        DraftProduct(name="Limonada de coco", price_cop=9000, description=None),
        DraftProduct(name="Jugo de mora", price_cop=7000, description="En agua o leche"),
    ]
    # No hint, no context line: just the instruction and the photo.
    [request] = spark.requests
    content = request.messages[1].content
    assert isinstance(content, tuple)
    assert [type(part) for part in content] == [TextPart, ImagePart]


@pytest.mark.parametrize(
    "item",
    [
        {"name": "Sin precio", "price_cop": None, "description": ""},
        {"name": "Gratis", "price_cop": 0, "description": ""},
        {"name": "Negativo", "price_cop": -5000, "description": ""},
        {"name": "Dos precios", "price_cop": "$5.000 o $7.000", "description": ""},
        {"name": "Decimal", "price_cop": 12.5, "description": ""},
        {"name": "", "price_cop": 5000, "description": ""},
        {"name": "x" * 121, "price_cop": 5000, "description": ""},
        {"price_cop": 5000},
        "Bandeja paisa $25.000",
    ],
    ids=[
        "no price",
        "zero",
        "negative",
        "two amounts",
        "fraction",
        "no name",
        "long name",
        "missing name",
        "not an object",
    ],
)
def test_art_iii_items_without_one_clear_price_or_name_are_dropped(item: object) -> None:
    assert parse_products(products(BANDEJA, item)) == [  # type: ignore[arg-type]
        DraftProduct(name="Bandeja paisa", price_cop=25000, description="Con chicharrón")
    ]


@pytest.mark.parametrize(
    "price",
    [
        PRICE_MAX + 1,
        99_999_999_999,
        1e12,
        "99999999999",
        "$9.999.999.999",
        "9" * 5000,
        "\u00b2",
    ],
    ids=[
        "one over",
        "misread digits",
        "float",
        "digits as text",
        "amount as text",
        "huge",
        "\u00b2",
    ],
)
def test_as_1_3_a_price_the_table_cannot_hold_drops_only_that_item(price: object) -> None:
    misread = {"name": "Misread", "price_cop": price, "description": ""}

    assert parse_products(products(BANDEJA, misread)) == [
        DraftProduct(name="Bandeja paisa", price_cop=25000, description="Con chicharr\u00f3n")
    ]


def test_as_1_3_the_largest_price_the_table_holds_is_kept() -> None:
    assert parse_products(products({**BANDEJA, "price_cop": PRICE_MAX})) == [
        DraftProduct(name="Bandeja paisa", price_cop=PRICE_MAX, description="Con chicharr\u00f3n")
    ]


@pytest.mark.parametrize(
    "description", ["Antes $9.999.999.999", "$" + "9" * 1990], ids=["over int4", "overflow"]
)
def test_as_1_3_a_description_with_an_amount_the_table_cannot_hold_is_left_out(
    description: str,
) -> None:
    # Every amount in a product's description is stored for the price validator, in int4 too.
    item = {"name": "Combo", "price_cop": 25000, "description": description}

    assert parse_products(products(item)) == [
        DraftProduct(name="Combo", price_cop=25000, description=None)
    ]


def test_an_answer_with_a_number_python_refuses_to_read_is_unusable() -> None:
    assert parse_products('{"products": [{"name": "x", "price_cop": ' + "9" * 5000 + "}]}") is None


def test_repeated_items_collapse_and_fenced_json_is_read() -> None:
    fenced = "```json\n" + products(BANDEJA, {**BANDEJA, "name": "BANDEJA  PAISA"}) + "\n```"

    assert parse_products(fenced) == [
        DraftProduct(name="Bandeja paisa", price_cop=25000, description="Con chicharrón")
    ]


def test_unreadable_answers_parse_to_none() -> None:
    for answer in ("", "No puedo leer la foto.", "{", '{"items": []}', '{"products": "x"}'):
        assert parse_products(answer) is None


async def test_an_unusable_answer_is_asked_again_once() -> None:
    spark = FakeSpark("No puedo leer la foto.", products(LIMONADA))

    drafts = await extract_products(MENU, None, chat=spark, model=MODEL)

    assert [draft.name for draft in drafts] == ["Limonada de coco"]
    assert len(spark.requests) == 2


async def test_two_unusable_answers_fail_the_job_for_good() -> None:
    spark = FakeSpark("???", "{")

    with pytest.raises(JobFailedError) as caught:
        await extract_products(MENU, None, chat=spark, model=MODEL)

    assert caught.value.code == "PROVIDER_ERROR"


async def test_a_photo_without_priced_products_fails_with_a_reason_for_the_seller() -> None:
    spark = FakeSpark(products())

    with pytest.raises(JobFailedError) as caught:
        await extract_products(MENU, None, chat=spark, model=MODEL)

    assert caught.value.code == "INSUFFICIENT_DATA"
    assert "precio" in caught.value.message


async def test_fr_003_imports_are_saved_as_drafts_and_skip_what_the_catalog_has() -> None:
    catalog = InMemoryCatalog()
    catalog.add_product(SELLER, "Bandeja paisa", price_cop=25000)
    drafts = [
        DraftProduct(name="bandeja PAISA", price_cop=25000, description=None),
        DraftProduct(name="Bandeja paisa", price_cop=27000, description=None),
        DraftProduct(name="Limonada de coco", price_cop=9000, description="Con hielo"),
    ]

    created = await save_drafts(catalog, SELLER, drafts)

    assert [(p.name, p.price_cop, p.status, p.source) for p in created] == [
        ("Bandeja paisa", 27000, "draft", "image_import"),
        ("Limonada de coco", 9000, "draft", "image_import"),
    ]
    assert created[1].description == "Con hielo"


async def test_fr_003_the_agent_never_finds_an_imported_draft() -> None:
    catalog = InMemoryCatalog()
    await save_drafts(
        catalog, SELLER, [DraftProduct(name="Limonada de coco", price_cop=9000, description=None)]
    )

    results = await catalog.search(SELLER, "limonada de coco")

    assert results == []


async def test_as_7_3_an_import_replays_from_its_cassette(tmp_path: Path) -> None:
    live = FakeSpark(products(BANDEJA, LIMONADA))
    recorded = await extract_products(
        MENU,
        None,
        chat=RecordedChatModel(Cassettes(tmp_path, CassetteMode.record), live),
        model=MODEL,
    )

    replayed = await extract_products(
        MENU, None, chat=RecordedChatModel(Cassettes(tmp_path, CassetteMode.replay)), model=MODEL
    )

    assert replayed == recorded
    assert len(live.requests) == 1


@pytest.mark.parametrize(
    ("data", "mime_type"),
    [
        (b"\xff\xd8\xff\xe0 jpeg", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n png", "image/png"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 webp", "image/webp"),
        (b"GIF89a gif", None),
        (b"%PDF-1.7 pdf", None),
        (b"", None),
    ],
)
def test_photos_are_recognized_by_their_bytes(data: bytes, mime_type: str | None) -> None:
    assert photo_type(data) == mime_type
