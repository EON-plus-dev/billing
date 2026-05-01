# ai-billing

Бібліотека для автоматичного обліку витрат на AI.

Автоматично визначає відповіді OpenAI, Anthropic та Google Gemini, розраховує вартість у USD за вбудованим прайсом і записує дебет-задачу в Redis.

## Встановлення

```bash
pip install git+https://github.com/EON-plus-dev/billing.git
```

Залежності: `redis[hiredis]>=5.0`, `pydantic>=2.0`, `aiohttp>=3.9`, `PyJWT>=2.0`.

## Швидкий старт

```python
from ai_billing import BillingClient

billing = BillingClient(redis_url="redis://localhost:6380", service_name="ai_chat")

# Перевірити баланс перед викликом AI
if await billing.has_credits(organization_id=123):
    response = await openai_client.chat.completions.create(model="gpt-4o-mini", messages=[...])
    await billing.report(response, organization_id=123, user_id=456)
```

## API

### `BillingClient(redis_url, service_name, *, fail_silently=True)`

Головний клієнт. Підключення до Redis ліниве — створюється при першому записі.

| Параметр | Тип | Опис |
|----------|-----|------|
| `redis_url` | `str` | URL Redis-сервера (`redis://host:port/db`) |
| `service_name` | `str` | Назва сервісу для поля `service` в дебет-записі |
| `fail_silently` | `bool` | `True` (за замовч.) — помилки логуються, не кидають exception. `False` — пробрасывает винятки |

---

### `await billing.report(response, *, organization_id, user_id, model_override=None)`

Автоматично визначає провайдера, витягує токени, рахує вартість і записує дебет в Redis.

```python
# OpenAI
response = await client.chat.completions.create(model="gpt-4o-mini", messages=[...])
usage = await billing.report(response, organization_id=1, user_id=2)

# Anthropic
response = await client.messages.create(model="claude-sonnet-4-5-20250929", messages=[...])
usage = await billing.report(response, organization_id=1, user_id=2)

# Gemini (потрібен model_override, бо Gemini SDK не завжди повертає model)
response = await model.generate_content_async("Hello")
usage = await billing.report(response, organization_id=1, user_id=2, model_override="gemini-2.5-flash")
```

**Повертає:** `UsageInfo | None` (None якщо `fail_silently=True` і виникла помилка)

---

### `await billing.report_tokens(model, *, input_tokens, output_tokens, thinking_output_tokens=0, organization_id, user_id)`

Ручне передавання кількості токенів. Корисно коли є тільки числа, а не об'єкт відповіді.

```python
usage = await billing.report_tokens(
    "gpt-4o-mini",
    input_tokens=500,
    output_tokens=200,
    organization_id=123,
    user_id=456,
)
# usage.cost_usd = Decimal('0.000195')
```

**Повертає:** `UsageInfo | None`

---

### `await billing.report_cost(cost_usd, *, organization_id, user_id)`

Прямий запис дебету із заданою вартістю. Без розрахунку токенів.

```python
await billing.report_cost(0.0035, organization_id=123, user_id=456)
```

**Повертає:** `None`

---

### `await billing.check_balance(organization_id)`

Читає закешований баланс кредитів з Redis (ключ `credits:org:{id}`).

```python
info = await billing.check_balance(organization_id=123)
if info:
    print(f"Balance: {info.balance} credits, tier: {info.subscription_tier}")
```

**Повертає:** `BalanceInfo | None` (None якщо кеш порожній або помилка при `fail_silently=True`)

> **Примітка:** дані з кешу можуть бути неактуальними (TTL кешу — до 30 хвилин).

---

### `await billing.has_credits(organization_id)`

Швидка перевірка: чи має організація позитивний баланс кредитів.

```python
if await billing.has_credits(organization_id=123):
    response = await openai_client.chat.completions.create(...)
    await billing.report(response, organization_id=123, user_id=456)
else:
    raise HTTPException(402, "Insufficient credits")
```

**Повертає:** `bool` — `True` якщо `balance > 0`, `False` якщо `balance <= 0` або кеш порожній.

---

### `BillingClient.calculate_cost(model, *, input_tokens=0, output_tokens=0, thinking_output_tokens=0)`

Статичний метод. Простий розрахунок вартості (без ПДВ, без cache, без розкладу). Підтримує `thinking_output_tokens` для Gemini-thinking моделей.

```python
from decimal import Decimal
from ai_billing import BillingClient

cost = BillingClient.calculate_cost("gpt-4o-mini", input_tokens=1_000_000)
# Decimal('0.150000')

cost = BillingClient.calculate_cost("gemini-2.5-flash", input_tokens=1000, thinking_output_tokens=5000)
# Decimal('0.012800')
```

**Повертає:** `Decimal` (USD, без ПДВ).

> Для cache-aware розрахунку з ПДВ і розкладом по компонентах див. модуль-рівневий `calculate_cost(model_id, usage)` нижче.

---

### `calculate_cost(model_id, usage) → CostBreakdown`

Модуль-рівнева функція. Розраховує вартість з ПДВ і розкладом по 4 компонентах: `input`, `output`, `cache_read`, `cache_write`. Призначена для cache-aware білінгу (Anthropic prompt caching) і бухгалтерських звітів.

```python
from ai_billing import calculate_cost, Usage

usage = Usage(
    input_tokens=1500,
    output_tokens=800,
    cached_input_tokens=12000,   # Anthropic prompt cache read
    cache_write_tokens=5000,     # Anthropic prompt cache write (5-min)
)
cb = calculate_cost("claude-sonnet-4-6", usage)

# CostBreakdown(
#     cost_no_vat=Decimal('0.039600'),
#     vat=Decimal('0.007920'),
#     cost_total=Decimal('0.047520'),
#     by_component={
#         'input':       Decimal('0.004500'),
#         'output':      Decimal('0.012000'),
#         'cache_read':  Decimal('0.003600'),
#         'cache_write': Decimal('0.018750'),
#     },
# )
```

**ПДВ-множник** береться з `VAT_MULTIPLIER` (env-overridable, default `1.20` — український ПДВ 20%). ПДВ застосовується тільки до **внутрішнього кост-каунтера** AI-операцій (Anthropic-як-нерезидент). До B2C топапів і пакетів ПДВ окремо НЕ додається.

**Cache pricing** є тільки в Anthropic-моделях. Для OpenAI/Gemini моделей `cached_input_tokens` і `cache_write_tokens` тихо коштують 0 — навіть якщо передано в `Usage`.

**Помилки:** при невідомому `model_id` кидається `UnknownModelError` (підклас і `BillingError`, і `ValueError`) з переліком валідних моделей у повідомленні.

**Повертає:** `CostBreakdown`.

---

### `await billing.close()`

Закриває Redis-з'єднання. Викликати при завершенні роботи сервісу.

---

## Моделі та ціни

Вбудований прайс (USD за 1M токенів, БЕЗ ПДВ). Дата звірки з docs провайдерів — у константі `MODEL_PRICING_VERIFIED_AT`.

| Модель | Input | Output | Thinking | Cache Read | Cache Write | Провайдер |
|--------|------:|-------:|---------:|-----------:|------------:|-----------|
| `gpt-4o` | $2.50 | $10.00 | — | — | — | OpenAI |
| `gpt-4o-mini` | $0.15 | $0.60 | — | — | — | OpenAI |
| `gpt-4.1-mini` | $0.40 | $1.60 | — | — | — | OpenAI |
| `gpt-4.1-nano` | $0.10 | $0.40 | — | — | — | OpenAI |
| `gpt-5-mini` | $0.25 | $2.00 | — | — | — | OpenAI |
| `gpt-5-nano` | $0.05 | $0.40 | — | — | — | OpenAI |
| `gpt-4` | $30.00 | $60.00 | — | — | — | OpenAI |
| `text-embedding-3-small` | $0.02 | $0.00 | — | — | — | OpenAI |
| `gemini-3-flash` | $0.10 | $0.40 | — | — | — | Google |
| `gemini-2.5-flash` | $0.30 | $2.50 | $2.50 | — | — | Google |
| `gemini-2.0-flash` | $0.10 | $0.40 | — | — | — | Google |
| `gemini-1.5-flash` | $0.075 | $0.30 | — | — | — | Google |
| `claude-sonnet-4-5-20250929` | $3.00 | $15.00 | — | $0.30 | $3.75 | Anthropic |
| `claude-sonnet-4-6` | $3.00 | $15.00 | — | $0.30 | $3.75 | Anthropic |
| `claude-haiku-4-5` | $1.00 | $5.00 | — | $0.10 | $1.25 | Anthropic |

Anthropic `cache_write` — це 5-хвилинний cache write (1.25× базової input-ціни). 1-годинний (2×) поки не моделюємо.

### Prefix matching

Версійовані назви моделей автоматично зіставляються з базовою назвою:

```
gpt-5-nano-2025-08-07  →  gpt-5-nano
gpt-4o-mini-2024-07-18 →  gpt-4o-mini
```

Якщо модель не знайдена — кидається `UnknownModelError` (або логується при `fail_silently=True`).

## Автодетекція провайдера

Бібліотека використовує duck typing — **не імпортує жодного AI SDK**. Працює з будь-якою версією SDK.

| Провайдер | Як визначає | Input tokens | Output tokens |
|-----------|-------------|:-------------|:--------------|
| **OpenAI** | `response.usage.prompt_tokens` існує | `response.usage.prompt_tokens` | `response.usage.completion_tokens` |
| **Anthropic** | `response.usage.input_tokens` + `response.stop_reason` | `response.usage.input_tokens` | `response.usage.output_tokens` |
| **Gemini** | `response.usage_metadata.prompt_token_count` існує | `response.usage_metadata.prompt_token_count` | `response.usage_metadata.candidates_token_count` |

Порядок перевірки: OpenAI → Anthropic → Gemini.

**Gemini:** відповіді часто не містять назву моделі — передавайте `model_override`.

## Redis-протокол

Кожен виклик `report()` / `report_tokens()` / `report_cost()` створює два записи в Redis одним pipeline:

```
SET debit:{operation_id} '<json>' EX 86400
SADD debit:queue {operation_id}
```

Payload JSON:

```json
{
  "organization_id": 123,
  "amount_usd": "0.000195",
  "service": "ai_chat",
  "user_id": 456,
  "operation_id": "a1b2c3d4e5f6...",
  "created_at": "2026-02-11T12:00:00+00:00"
}
```

- TTL ключа: 24 години
- `operation_id`: автогенерований UUID4 hex
- Pipeline: SET + SADD атомарно в одному round-trip

### Читання балансу

`check_balance()` / `has_credits()` читають кеш:

```
GET credits:org:{organization_id}
```

Значення — JSON з полями `balance`, `owner_id`, `subscription_tier`, `multiplier`, `updated_at`. Кеш оновлюється зовнішнім сервісом (TTL ~30 хв). Якщо ключ відсутній — повертається `None` / `False`.

## Схеми даних

### `UsageInfo`

Результат `report()` та `report_tokens()`:

```python
class UsageInfo(BaseModel):
    model: str                    # "gpt-4o-mini"
    input_tokens: int             # 500
    output_tokens: int            # 200
    thinking_output_tokens: int   # 0 (для Gemini thinking)
    cost_usd: Decimal             # Decimal('0.000195')
```

### `Usage`

Вхід для `calculate_cost(model_id, usage)`. Заморожений dataclass — для in-memory cost calculation.

```python
@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0   # Anthropic prompt cache read
    cache_write_tokens: int = 0    # Anthropic prompt cache write
```

Не містить `thinking_output_tokens` — для Gemini thinking використовуйте `BillingClient.calculate_cost(...)` зі старим API.

### `CostBreakdown`

Результат `calculate_cost(model_id, usage)`. Усі суми у USD, квантовані до 6 знаків після коми.

```python
@dataclass(frozen=True, slots=True)
class CostBreakdown:
    cost_no_vat: Decimal                    # сума 4 компонентів
    vat: Decimal                            # cost_no_vat * (VAT_MULTIPLIER - 1)
    cost_total: Decimal                     # cost_no_vat + vat
    by_component: dict[str, Decimal]        # {input, output, cache_read, cache_write}
```

### `BalanceInfo`

Результат `check_balance()`:

```python
class BalanceInfo(BaseModel):
    organization_id: int
    balance: int                          # кредити (не USD)
    owner_id: int | None                  # власник організації
    subscription_tier: str | None         # "premium", "basic", ...
    multiplier: Decimal | None            # множник дебету
    updated_at: datetime | None           # час оновлення кешу
```

### `DebitPayload`

Внутрішня модель для Redis-запису:

```python
class DebitPayload(BaseModel):
    organization_id: int
    amount_usd: Decimal           # до 6 знаків після коми
    service: str
    user_id: int
    operation_id: str             # auto: uuid4().hex
    created_at: datetime          # auto: UTC now
```

## Винятки

| Клас | Батько | Коли |
|------|--------|------|
| `BillingError` | `Exception` | Базовий для всіх |
| `ParseError` | `BillingError` | Не вдалося визначити провайдера або витягнути usage |
| `UnknownModelError` | `BillingError`, `ValueError` | Модель відсутня у прайсі. Multi-inheritance — ловиться як `BillingError` (legacy) і як `ValueError` (per ARCHITECTURE.md §7.3) |

При `fail_silently=True` (за замовчуванням) винятки логуються через `logging.getLogger("ai_billing")` і не пробрасываються далі — billing ніколи не ламає основну AI-операцію.

## Приклади інтеграції

### FastAPI endpoint з перевіркою балансу

```python
from fastapi import HTTPException
from ai_billing import BillingClient

billing = BillingClient(redis_url=settings.REDIS_URL, service_name="ai_chat")

@router.post("/chat")
async def chat(request: ChatRequest):
    if not await billing.has_credits(organization_id=request.organization_id):
        raise HTTPException(402, "Insufficient credits")

    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=request.messages,
    )

    await billing.report(
        response,
        organization_id=request.organization_id,
        user_id=request.user_id,
    )

    return {"text": response.choices[0].message.content}
```

### Gemini з thinking-токенами

```python
billing = BillingClient(redis_url="redis://localhost:6380", service_name="agreements")

response = await model.generate_content_async(prompt)
usage = await billing.report(response, organization_id=org_id, user_id=uid, model_override="gemini-2.5-flash")

if usage:
    print(f"Cost: ${usage.cost_usd}, thinking tokens: {usage.thinking_output_tokens}")
```

### Lifespan / shutdown

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await billing.close()

app = FastAPI(lifespan=lifespan)
```

### Розрахунок без запису

```python
from ai_billing import BillingClient

cost = BillingClient.calculate_cost("gpt-4", input_tokens=10_000, output_tokens=2_000)
# Decimal('0.420000')
```

## Розробка

```bash
cd billing
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

## Структура

```
billing/
├── pyproject.toml
├── README.md
├── src/
│   └── ai_billing/
│       ├── __init__.py          # Публічний API
│       ├── _version.py          # "0.4.0"
│       ├── client.py            # BillingClient
│       ├── pricing.py           # MODEL_PRICING + calculate_cost (cache+VAT) + _calculate_cost_legacy
│       ├── parsers.py           # Автодетекція OpenAI/Anthropic/Gemini
│       ├── http_transport.py    # HTTP fallback для check_balance
│       ├── redis_transport.py   # Async Redis writer (pipeline)
│       ├── schemas.py           # Usage, CostBreakdown, UsageInfo, BalanceInfo, DebitPayload
│       └── exceptions.py        # BillingError, ParseError, UnknownModelError
└── tests/
    ├── conftest.py
    ├── test_client.py
    ├── test_parsers.py
    ├── test_pricing.py
    ├── test_model_pricing.py    # Snapshot цін (захист від silent change)
    ├── test_calculate_cost.py   # VAT, components, overflow, ValueError
    ├── test_schemas.py
    ├── test_http_transport.py
    └── test_redis_transport.py
```
