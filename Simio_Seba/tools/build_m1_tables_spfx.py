#!/usr/bin/env python3
"""Build a Simio M1 model with imported bakery data tables.

This script creates a new .spfx file from the current M0 model. It injects the
data-table schemas and row XML fragments that Simio stores inside the .spfx zip.
It intentionally writes a new model file and leaves M0 untouched.
"""

from __future__ import annotations

import csv
import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIMIO_DIR = ROOT / "Simio_Seba"
INPUT_SPFX = SIMIO_DIR / "PanaderiaSupermercado_M0.spfx"
OUTPUT_SPFX = SIMIO_DIR / "PanaderiaSupermercado_M1_Datos.spfx"
DATA_DIR = SIMIO_DIR / "data_input"
MODEL_XML_PATH = "Models/Model_Panaderia/33334da2-4681-4d41-85d1-d4b500a37067.xml"
TABLE_DATA_PREFIX = "Models/Model_Panaderia/TableData"


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    default: str | None = None


@dataclass(frozen=True)
class TableSpec:
    name: str
    csv_name: str
    columns: tuple[Column, ...]
    key: str | None = None


TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "Tbl_ProductoIndice",
        "Tbl_ProductoIndice.csv",
        (
            Column("ProductoIdx", "Integer", "0"),
            Column("ProductoID", "String"),
            Column("NombreProducto", "String"),
            Column("FamiliaProceso", "String"),
            Column("FamiliaHorno", "Integer", "0"),
            Column("TiempoHorneadoMin", "Real", "0.0"),
            Column("KgBatchRef", "Real", "0.0"),
            Column("TempHornoC", "Real", "0.0"),
            Column("CapacidadKgPorCarroRef", "Real", "0.0"),
            Column("CarrosReqBatchRef", "Integer", "0"),
            Column("MinKgCompra", "Real", "0.0"),
            Column("ModaKgCompra", "Real", "0.0"),
            Column("MaxKgCompra", "Real", "0.0"),
            Column("Activo", "Boolean", "False"),
        ),
        "ProductoIdx",
    ),
    TableSpec(
        "Tbl_EtapasProducto",
        "Tbl_EtapasProducto.csv",
        (
            Column("ProductoIdx", "Integer", "0"),
            Column("ProductoID", "String"),
            Column("Secuencia", "Integer", "0"),
            Column("EtapaCod", "String"),
            Column("EtapaNombre", "String"),
            Column("TiempoMin", "Real", "0.0"),
            Column("UsaPanadero", "Boolean", "False"),
            Column("UsaManipulador", "Boolean", "False"),
            Column("UsaMaquina", "Boolean", "False"),
            Column("RecursoPreferente", "String"),
        ),
    ),
    TableSpec(
        "Tbl_DemandaHoraLong",
        "Tbl_DemandaHoraLong.csv",
        (
            Column("HoraIdx", "Integer", "0"),
            Column("FranjaHoraria", "String"),
            Column("HoraInicioMin", "Real", "0.0"),
            Column("HoraFinMin", "Real", "0.0"),
            Column("ProductoIdx", "Integer", "0"),
            Column("ProductoID", "String"),
            Column("KgObjetivoHora", "Real", "0.0"),
        ),
    ),
    TableSpec(
        "Tbl_ProbEleccionHoraLong",
        "Tbl_ProbEleccionHoraLong.csv",
        (
            Column("HoraIdx", "Integer", "0"),
            Column("FranjaHoraria", "String"),
            Column("HoraInicioMin", "Real", "0.0"),
            Column("HoraFinMin", "Real", "0.0"),
            Column("ProductoIdx", "Integer", "0"),
            Column("ProductoID", "String"),
            Column("Probabilidad", "Real", "0.0"),
        ),
    ),
    TableSpec(
        "Tbl_MixCompraCliente",
        "Tbl_MixCompraCliente.csv",
        (
            Column("NProductos", "Integer", "0"),
            Column("Probabilidad", "Real", "0.0"),
        ),
        "NProductos",
    ),
    TableSpec(
        "Tbl_StockPolitica",
        "Tbl_StockPolitica.csv",
        (
            Column("ProductoIdx", "Integer", "0"),
            Column("ProductoID", "String"),
            Column("StockInicialKg", "Real", "0.0"),
            Column("PuntoReposicionKg", "Real", "0.0"),
            Column("StockObjetivoKg", "Real", "0.0"),
            Column("CoberturaObjetivoMin", "Real", "0.0"),
            Column("PuntoReposicionMin", "Real", "0.0"),
            Column("SafetyFactor", "Real", "1.0"),
        ),
        "ProductoIdx",
    ),
    TableSpec(
        "Tbl_HornoFamilia",
        "Tbl_HornoFamilia.csv",
        (
            Column("FamiliaHorno", "Integer", "0"),
            Column("TiempoHorneadoMin", "Real", "0.0"),
            Column("MaxCarros", "Integer", "0"),
            Column("CargaMinKgBase", "Real", "0.0"),
            Column("EsperaMaxMin", "Real", "0.0"),
        ),
        "FamiliaHorno",
    ),
    TableSpec(
        "Tbl_SetupHorno",
        "Tbl_SetupHorno.csv",
        (
            Column("FamiliaDesde", "Integer", "0"),
            Column("FamiliaHasta", "Integer", "0"),
            Column("SetupMin", "Real", "0.0"),
        ),
    ),
    TableSpec(
        "Tbl_RecursosInstancia",
        "Tbl_RecursosInstancia.csv",
        (
            Column("RecursoID", "String"),
            Column("Tipo", "String"),
            Column("HomeNode", "String"),
            Column("VelocidadMps", "Real", "0.0"),
            Column("ScheduleID", "String"),
            Column("Activo", "Boolean", "False"),
        ),
        "RecursoID",
    ),
    TableSpec(
        "Tbl_PoliticasProduccion",
        "Tbl_PoliticasProduccion.csv",
        (
            Column("PoliticaID", "String"),
            Column("Tipo", "String"),
            Column("Descripcion", "String"),
            Column("Prioridad", "String"),
            Column("IntervaloLiberacionMin", "Real", "0.0"),
            Column("Activa", "Boolean", "False"),
        ),
        "PoliticaID",
    ),
    TableSpec(
        "Tbl_Escenarios",
        "Tbl_Escenarios.csv",
        (
            Column("EscenarioID", "String"),
            Column("NumPanaderos", "Integer", "0"),
            Column("NumManipuladores", "Integer", "0"),
            Column("NumHornos", "Integer", "0"),
            Column("NumMezcladoras", "Integer", "0"),
            Column("NumAmasadoras", "Integer", "0"),
            Column("PoliticaProd", "String"),
            Column("PoliticaHorno", "String"),
            Column("InicioProd", "String"),
            Column("DemandMult", "Real", "1.0"),
            Column("TransferMult", "Real", "1.0"),
            Column("StockInicialMult", "Real", "1.0"),
        ),
        "EscenarioID",
    ),
    TableSpec(
        "Tbl_Supuestos",
        "Tbl_Supuestos.csv",
        (
            Column("Parametro", "String"),
            Column("ValorBase", "String"),
            Column("Unidad", "String"),
            Column("Fuente", "String"),
            Column("Sensibilidad", "String"),
            Column("Confianza", "String"),
        ),
        "Parametro",
    ),
    TableSpec(
        "Tbl_RateClientesHora_Auditoria",
        "Rate_ClientesHora.csv",
        (
            Column("HoraIdx", "Integer", "0"),
            Column("BeginTime", "String"),
            Column("EndTime", "String"),
            Column("BeginMinute", "Real", "0.0"),
            Column("EndMinute", "Real", "0.0"),
            Column("RatePerHour", "Real", "0.0"),
            Column("KgObjetivoTotalHora", "Real", "0.0"),
            Column("KgEsperadoPorCliente", "Real", "0.0"),
        ),
        "HoraIdx",
    ),
)


PROPERTY_TAGS = {
    "String": "StringProperty",
    "Integer": "IntegerProperty",
    "Real": "RealProperty",
    "Boolean": "BooleanProperty",
}


def read_rows(csv_name: str) -> list[dict[str, str]]:
    path = DATA_DIR / csv_name
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalized_value(value: str, column: Column) -> str:
    value = (value or "").strip()
    if column.kind == "String":
        return value
    if column.kind == "Boolean":
        if value.lower() in {"true", "1", "yes", "si", "sí"}:
            return "True"
        if value.lower() in {"false", "0", "no"}:
            return "False"
        return column.default or "False"
    if column.kind == "Integer":
        if value == "":
            return column.default or "0"
        return str(int(float(value)))
    if column.kind == "Real":
        if value == "":
            return column.default or "0.0"
        return str(float(value))
    raise ValueError(f"Unsupported column kind: {column.kind}")


def schema_xml(spec: TableSpec) -> str:
    lines = ["        <Schema>", "          <PropertyDefinitions>"]
    for column in spec.columns:
        tag = PROPERTY_TAGS[column.kind]
        attrs = [f'Name="{html.escape(column.name)}"', 'NullString="null"']
        if column.kind in {"Integer", "Real", "Boolean"}:
            attrs.insert(1, f'DefaultValue="{column.default or "0"}"')
        lines.append(f"            <{tag} {' '.join(attrs)} />")
    lines.append("            <Overrides />")
    lines.append("          </PropertyDefinitions>")
    if spec.key:
        lines.append("          <Keys>")
        lines.append(f'            <Key Column="{html.escape(spec.key)}" />')
        lines.append("          </Keys>")
    lines.append("          <ExtendedAttributeSets>")
    for column in spec.columns:
        lines.append(
            f'            <ExtendedAttributeSet Name="{html.escape(column.name)}" '
            'OperationalPlanningEditable="True" />'
        )
    lines.append("          </ExtendedAttributeSets>")
    lines.append("        </Schema>")
    return "\n".join(lines)


def tables_xml() -> str:
    lines = ["  <Tables>"]
    for spec in TABLES:
        lines.append(f'    <Table Name="{html.escape(spec.name)}">')
        lines.append(schema_xml(spec))
        lines.append("      <Rows>")
        lines.append(f'        <FileRef Name="{TABLE_DATA_PREFIX}\\{html.escape(spec.name)}.xml" />')
        lines.append("      </Rows>")
        lines.append('      <DataBindings ImportMode="Automatic" />')
        lines.append("    </Table>")
    lines.append("  </Tables>")
    return "\n".join(lines)


def row_fragment_xml(spec: TableSpec) -> bytes:
    lines = ["<Fragment>"]
    for row in read_rows(spec.csv_name):
        lines.append("  <Row>")
        lines.append("    <Properties>")
        for column in spec.columns:
            value = normalized_value(row.get(column.name, ""), column)
            lines.append(
                f'      <Property Name="{html.escape(column.name)}">'
                f"{html.escape(value)}</Property>"
            )
        lines.append("    </Properties>")
        lines.append("  </Row>")
    lines.append("</Fragment>")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def rate_table_xml() -> str:
    rates = [0.0, 0.0, 0.0]
    for row in read_rows("Rate_ClientesHora.csv"):
        rates.append(float(row["RatePerHour"]))
    lines = [
        "  <RateTables>",
        f'    <RateTable Name="Rate_ClientesHora" IntervalSize="1" IntervalType="Hours" NumberIntervals="{len(rates)}">',
    ]
    for rate in rates:
        lines.append(f'      <Interval Value="{rate:.6f}" />')
    lines.append("    </RateTable>")
    lines.append("  </RateTables>")
    return "\n".join(lines)


def replace_or_insert_block(xml: str, tag: str, replacement: str, before_tag: str) -> str:
    pattern = re.compile(rf"\n\s*<{tag}>.*?</{tag}>", re.S)
    if pattern.search(xml):
        return pattern.sub(lambda _match: "\n" + replacement, xml, count=1)
    before = re.search(rf"\n\s*<{before_tag}>", xml)
    if not before:
        raise RuntimeError(f"Could not locate <{before_tag}> insertion point")
    return xml[: before.start()] + "\n" + replacement + xml[before.start() :]


def main() -> None:
    if not INPUT_SPFX.exists():
        raise FileNotFoundError(INPUT_SPFX)
    with zipfile.ZipFile(INPUT_SPFX, "r") as source:
        model_xml = source.read(MODEL_XML_PATH).decode("utf-8")
        model_xml = replace_or_insert_block(model_xml, "Tables", tables_xml(), "Schedules")
        model_xml = replace_or_insert_block(model_xml, "RateTables", rate_table_xml(), "Schedules")

        with zipfile.ZipFile(OUTPUT_SPFX, "w", compression=zipfile.ZIP_DEFLATED) as target:
            written = set()
            for info in source.infolist():
                if info.filename == MODEL_XML_PATH:
                    target.writestr(info, model_xml.encode("utf-8"))
                    written.add(info.filename)
                    continue
                if info.filename.startswith(f"{TABLE_DATA_PREFIX}/"):
                    continue
                target.writestr(info, source.read(info.filename))
                written.add(info.filename)
            for spec in TABLES:
                filename = f"{TABLE_DATA_PREFIX}/{spec.name}.xml"
                target.writestr(filename, row_fragment_xml(spec))
                written.add(filename)

    print(f"Wrote {OUTPUT_SPFX}")
    print(f"Injected {len(TABLES)} data tables and 1 rate table.")


if __name__ == "__main__":
    main()
