"""
G-Mini Agent — Conectores de información sin API key.

- Clima (Open-Meteo)
- Wikipedia
- Tipo de cambio (open.er-api.com)
- Noticias RSS/Atom
- Lector web (texto principal de una página)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import quote, urljoin

from backend.connectors.base import (
    Connector,
    ConnectorAction,
    ConnectorError,
    USER_AGENT,
    ConnectorField,
    ensure_public_url,
    read_body,
)

# Códigos WMO de Open-Meteo → descripción en español.
WMO_CODES: dict[int, str] = {
    0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa",
    56: "llovizna helada", 57: "llovizna helada intensa",
    61: "lluvia ligera", 63: "lluvia", 65: "lluvia fuerte",
    66: "lluvia helada", 67: "lluvia helada fuerte",
    71: "nieve ligera", 73: "nieve", 75: "nieve fuerte", 77: "granizo fino",
    80: "chubascos ligeros", 81: "chubascos", 82: "chubascos violentos",
    85: "chubascos de nieve", 86: "chubascos de nieve fuertes",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta fuerte con granizo",
}


class WeatherConnector(Connector):
    id = "weather"
    label = "Clima"
    description = "Clima actual y pronóstico de cualquier ciudad (Open-Meteo, sin API key)."
    category = "informacion"
    icon = "cloud-sun"
    docs_url = "https://open-meteo.com/en/docs"
    fields = [
        ConnectorField("default_city", "Ciudad por defecto", placeholder="Huancayo",
                       help="Se usa cuando no indicas ciudad."),
    ]

    def actions(self) -> list[ConnectorAction]:
        city = {"type": "string", "description": "Ciudad (ej. 'Huancayo' o 'Lima, Perú')."}
        return [
            ConnectorAction("current", "Clima actual de una ciudad.", self.current, {"city": city}),
            ConnectorAction(
                "forecast", "Pronóstico diario (1 a 14 días).", self.forecast,
                {"city": city, "days": {"type": "integer", "default": 3, "minimum": 1, "maximum": 14}},
            ),
        ]

    async def _geocode(self, city: str | None) -> dict[str, Any]:
        name = (city or self.setting("default_city") or "").strip()
        if not name:
            raise ConnectorError("Indica una ciudad.")
        query = name.split(",")[0].strip()
        data = await self.get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 5, "language": "es", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise ConnectorError(f"No encontré la ciudad '{name}'.")
        hint = name.split(",", 1)[1].strip().lower() if "," in name else ""
        if hint:
            for item in results:
                if hint in str(item.get("country", "")).lower() or hint in str(item.get("admin1", "")).lower():
                    return item
        return results[0]

    @staticmethod
    def _place(geo: dict[str, Any]) -> str:
        parts = [geo.get("name"), geo.get("admin1"), geo.get("country")]
        return ", ".join(p for p in parts if p)

    async def current(self, city: str | None = None) -> dict[str, Any]:
        geo = await self._geocode(city)
        data = await self.get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": geo["latitude"], "longitude": geo["longitude"], "timezone": "auto",
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                           "weather_code,wind_speed_10m,is_day",
            },
        )
        cur = data.get("current") or {}
        code = int(cur.get("weather_code") or 0)
        return {
            "lugar": self._place(geo),
            "hora_local": cur.get("time"),
            "condicion": WMO_CODES.get(code, f"código {code}"),
            "temperatura_c": cur.get("temperature_2m"),
            "sensacion_c": cur.get("apparent_temperature"),
            "humedad_pct": cur.get("relative_humidity_2m"),
            "precipitacion_mm": cur.get("precipitation"),
            "viento_kmh": cur.get("wind_speed_10m"),
        }

    async def forecast(self, city: str | None = None, days: int = 3) -> dict[str, Any]:
        geo = await self._geocode(city)
        data = await self.get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": geo["latitude"], "longitude": geo["longitude"], "timezone": "auto",
                "forecast_days": days,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,precipitation_sum,uv_index_max",
            },
        )
        daily = data.get("daily") or {}
        out = []
        for i, day in enumerate(daily.get("time") or []):
            code = int((daily.get("weather_code") or [0])[i] or 0)
            out.append({
                "fecha": day,
                "condicion": WMO_CODES.get(code, f"código {code}"),
                "max_c": (daily.get("temperature_2m_max") or [None])[i],
                "min_c": (daily.get("temperature_2m_min") or [None])[i],
                "prob_lluvia_pct": (daily.get("precipitation_probability_max") or [None])[i],
                "lluvia_mm": (daily.get("precipitation_sum") or [None])[i],
                "uv_max": (daily.get("uv_index_max") or [None])[i],
            })
        return {"lugar": self._place(geo), "dias": out}

    async def test(self) -> dict[str, Any]:
        result = await self.current(self.setting("default_city") or "Lima")
        return {"ok": True, "message": f"{result['lugar']}: {result['temperatura_c']} °C, {result['condicion']}"}


class WikipediaConnector(Connector):
    id = "wikipedia"
    label = "Wikipedia"
    description = "Busca artículos y obtiene resúmenes de Wikipedia en el idioma que elijas."
    category = "informacion"
    icon = "book-open"
    docs_url = "https://api.wikimedia.org/wiki/Core_REST_API"
    fields = [ConnectorField("language", "Idioma", placeholder="es", default="es")]

    def _lang(self, lang: str | None) -> str:
        value = (lang or self.setting("language") or "es").strip().lower()
        if not re.fullmatch(r"[a-z]{2,3}(-[a-z]+)?", value):
            raise ConnectorError("Código de idioma inválido.")
        return value

    def actions(self) -> list[ConnectorAction]:
        lang = {"type": "string", "description": "Código de idioma (es, en, pt…)."}
        return [
            ConnectorAction(
                "search", "Busca artículos por texto.", self.search,
                {"query": {"type": "string", "required": True}, "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10}, "lang": lang},
            ),
            ConnectorAction(
                "summary", "Resumen de un artículo por título exacto.", self.summary,
                {"title": {"type": "string", "required": True}, "lang": lang},
            ),
        ]

    async def search(self, query: str, limit: int = 5, lang: str | None = None) -> list[dict[str, Any]]:
        code = self._lang(lang)
        data = await self.get_json(
            f"https://{code}.wikipedia.org/w/rest.php/v1/search/page", params={"q": query, "limit": limit}
        )
        out = []
        for page in data.get("pages") or []:
            excerpt = re.sub(r"<[^>]+>", "", page.get("excerpt") or "")
            out.append({
                "titulo": page.get("title"),
                "descripcion": page.get("description"),
                "extracto": excerpt,
                "url": f"https://{code}.wikipedia.org/wiki/{quote(str(page.get('key') or ''))}",
            })
        return out

    async def summary(self, title: str, lang: str | None = None) -> dict[str, Any]:
        code = self._lang(lang)
        data = await self.get_json(
            f"https://{code}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}"
        )
        return {
            "titulo": data.get("title"),
            "descripcion": data.get("description"),
            "resumen": data.get("extract"),
            "url": ((data.get("content_urls") or {}).get("desktop") or {}).get("page"),
        }


class CurrencyConnector(Connector):
    id = "currency"
    label = "Tipo de cambio"
    description = "Convierte montos entre monedas (USD, PEN, EUR, MXN…) con tasas diarias."
    category = "finanzas"
    icon = "coins"
    docs_url = "https://www.exchangerate-api.com/docs/free"
    fields = [ConnectorField("base", "Moneda base", placeholder="PEN", default="PEN")]

    def actions(self) -> list[ConnectorAction]:
        return [
            ConnectorAction(
                "convert", "Convierte un monto entre dos monedas.", self.convert,
                {
                    "amount": {"type": "number", "required": True},
                    "from_currency": {"type": "string", "required": True, "description": "Código ISO, ej. USD"},
                    "to_currency": {"type": "string", "required": True, "description": "Código ISO, ej. PEN"},
                },
            ),
            ConnectorAction(
                "rates", "Tasas de una moneda base frente a otras.", self.rates,
                {"base": {"type": "string"}, "symbols": {"type": "string", "description": "Lista separada por comas"}},
            ),
        ]

    @staticmethod
    def _code(value: str) -> str:
        code = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ConnectorError(f"Código de moneda inválido: '{value}'.")
        return code

    async def _latest(self, base: str) -> dict[str, Any]:
        data = await self.get_json(f"https://open.er-api.com/v6/latest/{self._code(base)}")
        if data.get("result") != "success":
            raise ConnectorError(f"No hay tasas para {base}.")
        return data

    async def convert(self, amount: float, from_currency: str, to_currency: str) -> dict[str, Any]:
        src, dst = self._code(from_currency), self._code(to_currency)
        data = await self._latest(src)
        rate = (data.get("rates") or {}).get(dst)
        if rate is None:
            raise ConnectorError(f"No hay tasa de {src} a {dst}.")
        return {
            "monto": amount, "de": src, "a": dst, "tasa": rate,
            "resultado": round(float(amount) * float(rate), 4),
            "actualizado": data.get("time_last_update_utc"),
            "fuente": "open.er-api.com",
        }

    async def rates(self, base: str | None = None, symbols: str | None = None) -> dict[str, Any]:
        code = self._code(base or self.setting("base") or "USD")
        data = await self._latest(code)
        rates = data.get("rates") or {}
        if symbols:
            wanted = {self._code(s) for s in symbols.split(",") if s.strip()}
            rates = {k: v for k, v in rates.items() if k in wanted}
        return {"base": code, "tasas": rates, "actualizado": data.get("time_last_update_utc")}


# User-Agent descriptivo con contacto: Wikipedia y otros sitios rechazan los que
# imitan a un navegador sin serlo (política de bots de Wikimedia).
_PAGE_HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


async def fetch_public_text(connector: Connector, url: str, *, max_redirects: int = 5) -> tuple[str, str]:
    """GET con validación anti-SSRF en cada redirección. Devuelve (url_final, texto)."""
    import aiohttp

    current = await ensure_public_url(url)
    for _ in range(max_redirects + 1):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.get(current, allow_redirects=False, headers=_PAGE_HEADERS) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if not location:
                        raise ConnectorError("Redirección sin destino.")
                    current = await ensure_public_url(urljoin(current, location))
                    continue
                if resp.status >= 400:
                    raise ConnectorError(f"HTTP {resp.status} al abrir {current}.")
                raw = await read_body(resp, limit=3 * 1024 * 1024)
                return current, raw.decode(resp.charset or "utf-8", errors="replace")
    raise ConnectorError("Demasiadas redirecciones.")


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None and node.text else ""


def parse_feed(xml_text: str, limit: int = 10) -> dict[str, Any]:
    """RSS 2.0 / Atom → {titulo, items[]}. Sin entidades externas (expat no las resuelve)."""
    if "<!ENTITY" in xml_text[:4096].upper():
        raise ConnectorError("El feed declara entidades XML; se rechaza por seguridad.")
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ConnectorError(f"El feed no es XML válido: {exc}") from exc
    atom = "{http://www.w3.org/2005/Atom}"
    items: list[dict[str, Any]] = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        channel = root.find("channel")
        title = _text(channel.find("title")) if channel is not None else ""
        for item in (channel.findall("item") if channel is not None else [])[:limit]:
            items.append({
                "titulo": _text(item.find("title")),
                "enlace": _text(item.find("link")),
                "fecha": _text(item.find("pubDate")),
                "resumen": re.sub(r"<[^>]+>", "", _text(item.find("description")))[:400],
            })
    elif root.tag == f"{atom}feed":
        title = _text(root.find(f"{atom}title"))
        for entry in root.findall(f"{atom}entry")[:limit]:
            link = entry.find(f"{atom}link")
            items.append({
                "titulo": _text(entry.find(f"{atom}title")),
                "enlace": link.get("href", "") if link is not None else "",
                "fecha": _text(entry.find(f"{atom}updated")) or _text(entry.find(f"{atom}published")),
                "resumen": re.sub(r"<[^>]+>", "", _text(entry.find(f"{atom}summary")))[:400],
            })
    else:
        raise ConnectorError("Formato de feed no reconocido (se espera RSS o Atom).")
    return {"titulo": title, "items": items}


class RssConnector(Connector):
    id = "rss"
    label = "Noticias (RSS)"
    description = "Lee titulares de cualquier feed RSS o Atom y de tus fuentes favoritas."
    category = "informacion"
    icon = "rss"
    fields = [
        ConnectorField("feeds", "Feeds favoritos", kind="textarea",
                       placeholder="https://elcomercio.pe/arcio/rss/\nhttps://feeds.bbci.co.uk/mundo/rss.xml",
                       help="Una URL por línea. El agente los usa con la acción 'favorites'."),
    ]

    def actions(self) -> list[ConnectorAction]:
        return [
            ConnectorAction(
                "read", "Lee un feed RSS/Atom por URL.", self.read,
                {"url": {"type": "string", "required": True}, "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50}},
            ),
            ConnectorAction(
                "favorites", "Titulares de los feeds favoritos configurados.", self.favorites,
                {"limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20}},
            ),
        ]

    async def read(self, url: str, limit: int = 10) -> dict[str, Any]:
        final_url, text = await fetch_public_text(self, url)
        data = parse_feed(text, limit)
        data["url"] = final_url
        return data

    async def favorites(self, limit: int = 5) -> list[dict[str, Any]]:
        urls = [u.strip() for u in str(self.setting("feeds") or "").splitlines() if u.strip()]
        if not urls:
            raise ConnectorError("No hay feeds favoritos configurados (Ajustes > Conectores > Noticias).")
        out = []
        for url in urls[:10]:
            try:
                out.append(await self.read(url, limit))
            except ConnectorError as exc:
                out.append({"url": url, "error": str(exc)})
        return out


class WebReaderConnector(Connector):
    id = "web_reader"
    label = "Lector web"
    description = "Extrae el título y el texto principal de una página (sin anuncios ni menús)."
    category = "informacion"
    icon = "file-text"

    def actions(self) -> list[ConnectorAction]:
        return [
            ConnectorAction(
                "read", "Texto principal de una URL pública.", self.read,
                {"url": {"type": "string", "required": True}, "max_chars": {"type": "integer", "default": 12000, "minimum": 500, "maximum": 60000}},
            ),
        ]

    async def read(self, url: str, max_chars: int = 12000) -> dict[str, Any]:
        from bs4 import BeautifulSoup

        final_url, html = await fetch_public_text(self, url)
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
        main = soup.find("article") or soup.find("main") or soup.body or soup
        blocks = []
        for el in main.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote"]):
            text = " ".join(el.get_text(" ", strip=True).split())
            if len(text) >= 3:
                prefix = "#" * int(el.name[1]) + " " if el.name in ("h1", "h2", "h3") else ""
                blocks.append(prefix + text)
        content = "\n".join(blocks) or " ".join(main.get_text(" ", strip=True).split())
        truncated = len(content) > max_chars
        return {
            "url": final_url,
            "titulo": title,
            "texto": content[:max_chars],
            "recortado": truncated,
            "obtenido": datetime.now().isoformat(timespec="seconds"),
        }
