"""
G-Mini Agent — Conectores regionales sin API key.

- Tipo de cambio oficial del Perú (BCRP): SBS (el que usa SUNAT) e interbancario.
- Feriados nacionales de cualquier país (Nager.Date).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from backend.connectors.base import Connector, ConnectorAction, ConnectorError, ConnectorField

_BCRP_URL = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/{series}/json/{start}/{end}/esp"
_BCRP_SERIES = {
    # Promedio ponderado del sistema bancario (SBS): referencia para SUNAT.
    "sbs": ("PD04639PD", "PD04640PD", "Sistema bancario SBS (referencia SUNAT)"),
    "interbancario": ("PD04637PD", "PD04638PD", "Interbancario (cierre BCRP)"),
}
_MONTHS = {"Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6, "Jul": 7, "Ago": 8,
           "Set": 9, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12}


def parse_bcrp_period(name: str) -> date:
    """'07.Oct.26' -> date(2026, 10, 7)."""
    try:
        day, month, year = name.split(".")
        return date(2000 + int(year), _MONTHS[month[:3].capitalize()], int(day))
    except (ValueError, KeyError) as exc:
        raise ConnectorError(f"Fecha del BCRP no reconocida: {name}") from exc


def _number(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None  # "n.d." = aún no publicado


class BcrpExchangeConnector(Connector):
    id = "bcrp"
    label = "Tipo de cambio Perú (BCRP)"
    description = "Dólar oficial en soles publicado por el BCRP: SBS (el que usa SUNAT) e interbancario."
    category = "finanzas"
    icon = "landmark"
    docs_url = "https://estadisticas.bcrp.gob.pe/estadisticas/series/ayuda/api"

    def actions(self) -> list[ConnectorAction]:
        kind = {"type": "string", "default": "sbs", "description": "sbs (referencia SUNAT) o interbancario"}
        return [
            ConnectorAction("today", "Último tipo de cambio publicado (compra y venta).", self.today, {"kind": kind}),
            ConnectorAction(
                "range", "Tipo de cambio diario entre dos fechas (AAAA-MM-DD).", self.range,
                {"start": {"type": "string", "required": True}, "end": {"type": "string", "required": True}, "kind": kind},
            ),
        ]

    async def _series(self, kind: str, start: date, end: date) -> tuple[str, list[dict[str, Any]]]:
        if kind not in _BCRP_SERIES:
            raise ConnectorError("kind debe ser 'sbs' o 'interbancario'.")
        if end < start or (end - start).days > 400:
            raise ConnectorError("Rango inválido (máximo 400 días).")
        buy, sell, label = _BCRP_SERIES[kind]
        data = await self.get_json(_BCRP_URL.format(series=f"{buy}-{sell}", start=start, end=end))
        rows = []
        for period in (data or {}).get("periods") or []:
            values = period.get("values") or []
            compra, venta = _number(values[0] if values else None), _number(values[1] if len(values) > 1 else None)
            if compra is None and venta is None:
                continue
            rows.append({"fecha": parse_bcrp_period(period["name"]).isoformat(), "compra": compra, "venta": venta})
        return label, rows

    async def today(self, kind: str = "sbs") -> dict[str, Any]:
        end = date.today()
        label, rows = await self._series(kind, end - timedelta(days=10), end)
        if not rows:
            raise ConnectorError("El BCRP no publicó datos en los últimos 10 días.")
        last = rows[-1]
        return {"tipo": label, "moneda": "S/ por US$", **last}

    async def range(self, start: str, end: str, kind: str = "sbs") -> dict[str, Any]:
        try:
            start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ConnectorError("Usa fechas AAAA-MM-DD.") from exc
        label, rows = await self._series(kind, start_d, end_d)
        return {"tipo": label, "moneda": "S/ por US$", "dias": rows}

    async def test(self) -> dict[str, Any]:
        result = await self.today()
        return {"ok": True, "message": f"{result['fecha']}: compra {result['compra']} / venta {result['venta']}"}


class HolidaysConnector(Connector):
    id = "holidays"
    label = "Feriados"
    description = "Feriados nacionales de cualquier país y año, y el próximo feriado (Nager.Date)."
    category = "informacion"
    icon = "calendar-days"
    docs_url = "https://date.nager.at/Api"
    fields = [ConnectorField("country", "País por defecto (código ISO)", placeholder="PE", default="PE")]

    def actions(self) -> list[ConnectorAction]:
        country = {"type": "string", "description": "Código ISO de 2 letras (PE, MX, CO, AR, CL, ES...)"}
        return [
            ConnectorAction("list", "Feriados de un año.", self.list,
                            {"year": {"type": "integer", "minimum": 1975, "maximum": 2100}, "country": country}),
            ConnectorAction("next", "Próximos feriados desde hoy.", self.next,
                            {"country": country, "count": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10}}),
        ]

    def _country(self, country: str | None) -> str:
        code = str(country or self.setting("country") or "PE").strip().upper()
        if len(code) != 2 or not code.isalpha():
            raise ConnectorError("País inválido: usa el código ISO de 2 letras (ej. PE).")
        return code

    async def _year(self, year: int, country: str) -> list[dict[str, Any]]:
        data = await self.get_json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}")
        return [
            {"fecha": item.get("date"), "nombre": item.get("localName") or item.get("name"),
             "nacional": bool(item.get("global", True))}
            for item in data or []
        ]

    async def list(self, year: int | None = None, country: str | None = None) -> dict[str, Any]:
        code = self._country(country)
        year = year or datetime.now().year
        return {"pais": code, "anio": year, "feriados": await self._year(year, code)}

    async def next(self, country: str | None = None, count: int = 3) -> dict[str, Any]:
        code = self._country(country)
        today = date.today()
        upcoming = [h for h in await self._year(today.year, code) if h["fecha"] and h["fecha"] >= today.isoformat()]
        if len(upcoming) < count:
            upcoming += await self._year(today.year + 1, code)
        items = sorted(upcoming, key=lambda h: h["fecha"])[:count]
        for item in items:
            item["faltan_dias"] = (date.fromisoformat(item["fecha"]) - today).days
        return {"pais": code, "proximos": items}
