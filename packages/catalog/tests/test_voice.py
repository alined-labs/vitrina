from vitrina_catalog.voice import redact, seller_lines, voice_profile

ANDROID_EXPORT = """27/10/26, 14:02 - Los mensajes y las llamadas están cifrados de extremo a extremo.
27/10/26, 14:05 - Ana Bolsos: Hola veci! ¿Cómo vas? 😊
27/10/26, 14:06 - Cliente Uno: Buenas, ¿cuánto vale el bolso rojo?
27/10/26, 14:07 - Ana Bolsos: Ese bolsito vale $85.000, hecho a mano 💛
y te lo envío hoy mismo
27/10/26, 14:08 - Cliente Uno: Mi número es 3000000001
27/10/26, 14:09 - Ana Bolsos: <Multimedia omitido>
27/10/26, 14:10 - Ana Bolsos: Listo veci, escríbeme al 300 000 0002 o a ana@correo.example
"""

IOS_EXPORT = """[27/10/26, 2:05:33 p. m.] Ana Bolsos: Con mucho gusto, a la orden
[27/10/26, 2:06:10 p. m.] Cliente Dos: gracias
"""


def test_as_1_5_only_the_sellers_own_lines_are_kept() -> None:
    lines = seller_lines(ANDROID_EXPORT, "Ana Bolsos")

    assert lines == [
        "Hola veci! ¿Cómo vas? 😊",
        "Ese bolsito vale $85.000, hecho a mano 💛\ny te lo envío hoy mismo",
        "Listo veci, escríbeme al 300 000 0002 o a ana@correo.example",
    ]
    assert seller_lines(IOS_EXPORT, "ana bolsos") == ["Con mucho gusto, a la orden"]


def test_as_1_5_customer_lines_never_survive_even_with_similar_names() -> None:
    assert seller_lines(ANDROID_EXPORT, "Cliente") == []
    assert all("cuánto vale" not in line for line in seller_lines(ANDROID_EXPORT, "Ana Bolsos"))


def test_as_1_5_pii_is_redacted_but_prices_stay() -> None:
    text = "Escríbeme al 300 000 0002, al +57 300 000 0003 o a ana@correo.example; cédula 1234567890; @ana.bolsos; vale $85.000"

    assert redact(text) == (
        "Escríbeme al [teléfono], al [teléfono] o a [correo]; cédula [número]; [usuario]; vale $85.000"
    )


def test_fr_004_voice_profile_reads_tone_emoji_and_regionalisms() -> None:
    casual = voice_profile(
        [
            "Hola veci! ¿Cómo vas? 😊",
            "Ese bolsito vale $85.000, hecho a mano 💛",
            "Listo veci, de una",
            "¡Gracias mi reina!",
        ]
    )
    formal = voice_profile(
        [
            "Buenos días, ¿en qué le puedo colaborar?",
            "Usted puede recogerlo en la tienda.",
            "Con gusto le confirmo el pedido.",
        ]
    )

    assert casual.formality is not None
    assert formal.formality is not None
    assert casual.formality < formal.formality
    assert casual.warmth > formal.warmth
    assert casual.emoji_rate == 0.5
    assert {"veci", "de una", "mi reina"} <= set(casual.regionalisms)
    assert casual.greeting_style == "hola veci"
    assert casual.sample_count == 4
    assert 1 <= casual.formality <= 5
    assert 1 <= formal.warmth <= 5


async def test_the_engine_reads_settings_the_public_seller_omits() -> None:
    from vitrina_catalog.api import SellerSettings
    from vitrina_catalog.sellers import InMemorySellerStore, SellerCreate

    store = InMemorySellerStore()
    seller = await store.create(
        SellerCreate(
            business_name="Bolsos Ana",
            owner_name="Ana",
            whatsapp_number="+573000000001",
            city="Cali",
        ),
        auth_subject=None,
    )

    settings = await store.settings(seller.id)

    assert isinstance(settings, SellerSettings)
    assert (
        settings.owner_name,
        settings.low_confidence_threshold,
        settings.handoff_amount_threshold_cop,
    ) == (
        "Ana",
        0.6,
        300000,
    )
