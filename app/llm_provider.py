"""Pool de API keys de Gemini con rotación automática.

Lee todas las variables de entorno que matchean GEMINI_API_KEY / GEMINI_API_KEY_N
y, si una key se queda sin cuota (rate limit) o quedó inválida/revocada, reintenta
automáticamente con la siguiente key del pool antes de fallar el nodo.

No se loguea ni se persiste nunca el valor de las keys, solo su índice en el pool.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

_KEY_VAR_PATTERN = re.compile(r"^GEMINI_API_KEY(_\d+)?$")

_FALLBACK_ERROR_HINTS = (
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "resourceexhausted",
    "429",
    "exceeded",
    "permission_denied",
    "permission denied",
    "api_key_invalid",
    "invalid api key",
    "unauthenticated",
)


def _load_gemini_keys() -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for name, value in os.environ.items():
        if _KEY_VAR_PATTERN.match(name) and value and value not in seen:
            seen.add(value)
            keys.append(value)
    return keys


def _is_fallback_worthy(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _FALLBACK_ERROR_HINTS)


class RotatingGeminiLLM:
    """Envoltorio con la misma interfaz mínima que un chat model de LangChain
    (`await llm.ainvoke(messages) -> response.content`), pero que prueba varias
    API keys en orden hasta encontrar una que funcione.
    """

    def __init__(self, model_name: str, keys: list[str]):
        if not keys:
            raise RuntimeError(
                "No hay ninguna GEMINI_API_KEY configurada. Definí GEMINI_API_KEY "
                "(y opcionalmente GEMINI_API_KEY_1, _2, ...) en tu .env."
            )
        self._model_name = model_name
        self._keys = keys
        self._index = 0
        self._clients: dict[int, object] = {}

    def _client_for(self, index: int):
        if index not in self._clients:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._clients[index] = ChatGoogleGenerativeAI(
                model=self._model_name, google_api_key=self._keys[index], temperature=0
            )
        return self._clients[index]

    async def ainvoke(self, messages):
        last_exc: Exception | None = None
        for attempt in range(len(self._keys)):
            index = (self._index + attempt) % len(self._keys)
            client = self._client_for(index)
            try:
                response = await client.ainvoke(messages)
                if index != self._index:
                    logger.info(
                        "Gemini: se cambió a la API key #%d del pool (la anterior falló).",
                        index,
                    )
                self._index = index
                return response
            except Exception as exc:  # noqa: BLE001
                if not _is_fallback_worthy(exc):
                    raise
                logger.warning(
                    "Gemini: la API key #%d falló (cuota/rate-limit/inválida), "
                    "probando la siguiente del pool.",
                    index,
                )
                last_exc = exc
                continue

        raise RuntimeError(
            f"Se agotaron las {len(self._keys)} GEMINI_API_KEY configuradas."
        ) from last_exc


_pool_cache: dict[str, RotatingGeminiLLM] = {}


def get_gemini_llm(model_name: str) -> RotatingGeminiLLM:
    if model_name not in _pool_cache:
        _pool_cache[model_name] = RotatingGeminiLLM(model_name, _load_gemini_keys())
    return _pool_cache[model_name]
