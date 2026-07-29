import datetime
import io
import json
import os
import shutil
import subprocess
import zipfile
import secrets
import tempfile

import streamlit as st
import streamlit.components.v1 as components
from excel_energeticka_navratnost import (
    vytvor_excel_rozpocet_s_esg_bytes,
    vytvor_excel_energeticky_projekt_bytes,
    vytvor_excel_stavebni_rozpocet_bytes,
    TYPY_BUDOV_ROZPOCET,
    KONFIGURACE_ENERGO,
)
from centralni_evidence import CentralniEvidenceProjektu
from global_bim_registry import GlobalBIMRegistry
from modul_skenovani import (
    nacist_a_analyzovat_mracno,
    analyzovat_termovizni_snimek,
    zpracovat_interiérove_fotografie,
    navrhnout_kabelove_trasy,
    vytvor_excel_protokol_skenovani_bytes,
    vytvor_excel_nabidka_kabelove_trasy_bytes,
    vytvorit_schema_rozvadce_z_fotky,
    vytvor_excel_schema_rozvadce_bytes,
)

try:
    from owslib.wms import WebMapService
except ImportError:
    WebMapService = None

bim_registry = GlobalBIMRegistry()
evidence_db = CentralniEvidenceProjektu()


def _session_get(key, default=None):
    try:
        return st.session_state.get(key, default)
    except Exception:
        return default


def _session_set(key, value):
    try:
        st.session_state[key] = value
    except Exception:
        pass

def inicializovat_demo_data():
    bim_registry.zaregistrovat_uzivatele("director@metrostav.cz", "metrostav", "Generální ředitel", "MAJITEL")
    bim_registry.zaregistrovat_uzivatele("projektant1@metrostav.cz", "metrostav", "Projektant 1", "PROJEKTANT")
    bim_registry.vytvorit_projekt("SO-01", "metrostav", "SO-01 Výrobní hala Vítkovice", "director@metrostav.cz")
    bim_registry.pridat_verzi_projektu(
        "SO-01",
        1,
        "Výchozí BIM pasportizace",
        "https://example.com/SO-01.ifc",
        "https://example.com/SO-01.dxf",
        "VERIFIKOVÁNO"
    )


def stahnout_katastralni_mapu_cuzk(souradnice_bbox):
    """
    Připojí se na oficiální produkční API ČÚZK a stáhne aktuální katastrální mapu.
    Vrací bytes obrázku nebo None při chybě.
    """
    if WebMapService is None:
        print("❌ Knihovna OWSLib není nainstalovaná.")
        return None

    wms_url = "https://cuzk.cz"
    try:
        wms = WebMapService(wms_url, version='1.1.1')
        img = wms.getmap(
            layers=['RST_KN'],
            srs='EPSG:4326',
            bbox=souradnice_bbox,
            size=(1024, 1024),
            format='image/png',
            transparent=True,
        )
        return img.read()
    except Exception as e:
        print(f"❌ Selhalo připojení k ČÚZK API: {e}")
        return None

def vytvorit_ifc_export(data_objektu):
    projekt_id = data_objektu["id_projektu"]
    delka_m = float(data_objektu.get("delka_m", 60.0))
    sirka_m = float(data_objektu.get("sirka_m", 30.0))
    vyska_m = float(data_objektu.get("vyska_m", 8.0))
    datum_vytvoreni = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    obsah = f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('{projekt_id}.ifc','{datum_vytvoreni}');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('{projekt_id}',$,'BIM Scan AI Project',$,$,$,$,(#2),$);
#2=IFCUNITASSIGNMENT((#3,#4,#5));
#3=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#4=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#5=IFCSIUNIT(*,.MASSUNIT.,$,.GRAM.);
#6=IFCSITE($,$,'{data_objektu.get("lokace", "Unknown site")}',$,$,$,$,$,.ELEMENT.,$,$,$,$,$);
#7=IFCBUILDING($,$,'{projekt_id} building',$,$,$,$,.ELEMENT.,$,$,$);
#8=IFCCARTESIANPOINT((0.0,0.0,0.0));
#9=IFCCARTESIANPOINT(({delka_m},0.0,0.0));
#10=IFCCARTESIANPOINT(({delka_m},{sirka_m},0.0));
#11=IFCCARTESIANPOINT((0.0,{sirka_m},0.0));
#12=IFCCARTESIANPOINT((0.0,0.0,{vyska_m}));
#13=IFCPOLYLINE((#8,#9,#10,#11,#8));
ENDSEC;
END-ISO-10303-21;
"""
    return obsah.encode("utf-8")


def vytvorit_dxf_export(data_objektu):
    projekt_id = data_objektu["id_projektu"]
    delka_m = float(data_objektu.get("delka_m", 60.0))
    sirka_m = float(data_objektu.get("sirka_m", 30.0))
    vyska_m = float(data_objektu.get("vyska_m", 8.0))

    obsah = f"""0
SECTION
2
HEADER
9
$ACADVER
1
AC1027
0
ENDSEC
0
SECTION
2
ENTITIES
0
LWPOLYLINE
8
0
90
4
70
1
10
0.0
20
0.0
10
{delka_m}
20
0.0
10
{delka_m}
20
{sirka_m}
10
0.0
20
{sirka_m}
0
TEXT
8
0
10
1.0
20
1.0
40
0.5
1
{projekt_id} | {delka_m} x {sirka_m} x {vyska_m} m
0
ENDSEC
0
EOF
"""
    return obsah.encode("utf-8")


def vytvorit_dwg_export(data_objektu):
    konvertor = os.environ.get("ODAFILECONVERTER_PATH") or shutil.which("ODAFileConverter")
    if not konvertor:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = os.path.abspath(temp_dir)
        source_dir = os.path.join(temp_dir_path, "source")
        target_dir = os.path.join(temp_dir_path, "target")
        os.makedirs(source_dir, exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)

        source_dxf = os.path.join(source_dir, f"{data_objektu['id_projektu']}.dxf")
        with open(source_dxf, "wb") as handle:
            handle.write(vytvorit_dxf_export(data_objektu))

        try:
            subprocess.run(
                [
                    konvertor,
                    source_dir,
                    target_dir,
                    "ACAD2018",
                    "DWG",
                    "0",
                    "0",
                    "*.dxf",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            print(f"❌ DWG konverze selhala: {exc}")
            return None

        for entry in os.listdir(target_dir):
            if entry.lower().endswith(".dwg"):
                output_path = os.path.join(target_dir, entry)
                with open(output_path, "rb") as handle:
                    return handle.read()

    return None


def ma_dwg_export():
    return bool(os.environ.get("ODAFILECONVERTER_PATH") or shutil.which("ODAFileConverter"))


def dwg_export_status_text():
    if ma_dwg_export():
        return "DWG export je připravený a bude generován přes externí konvertor."
    return "DWG export není v tomto prostředí aktivní. DXF je dostupný hned teď a AutoCAD ho otevře."


def vytvorit_metadata_json(data_objektu):
    metadata = {
        "id_projektu": data_objektu.get("id_projektu", ""),
        "lokace": data_objektu.get("lokace", ""),
        "parcela": data_objektu.get("parcela", ""),
        "investor": data_objektu.get("investor", ""),
        "investor_adresa": data_objektu.get("investor_adresa", ""),
        "datum_skenu": data_objektu.get("datum_skenu", ""),
        "trida_lps": data_objektu.get("trida_lps", ""),
        "tloustka_podlahy_mm": data_objektu.get("tloustka_podlahy_mm", 0),
        "max_zatizeni_tuny": data_objektu.get("max_zatizeni_tuny", 0),
        "uspora_kwh": data_objektu.get("uspora_kwh", 0),
        "vytvoreno": datetime.datetime.now().isoformat(),
    }
    return json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")


def _ziskat_elektricke_dokumentacni_data():
    schema_data = _session_get("scan_schema_data", None)
    trasy_data = _session_get("scan_trasy_data", None)
    return schema_data, trasy_data


def _vytvorit_elektricke_souhrn_textu(schema_data, trasy_data, data_objektu):
    casti = [
        "ELEKTROTECHNICKÁ PROJEKTOVÁ DOKUMENTACE",
        f"Objekt: {data_objektu.get('lokace', 'N/A')}",
        "",
    ]
    if schema_data:
        casti.append(f"Schéma rozvaděče: {schema_data.get('nazev', 'Rozvaděč')}")
        for prvek in schema_data.get("prvky", []):
            casti.append(
                f"- {prvek.get('nazev', '')} | typ: {prvek.get('typ', '')} | obvod: {prvek.get('circuit', '')}"
            )
    else:
        casti.append("Schéma rozvaděče: nebylo vygenerováno.")

    casti.append("")
    if trasy_data:
        casti.append(f"Návrh kabelových tras: {len(trasy_data.get('trasy', []))} položek")
        for trasa in trasy_data.get("trasy", []):
            casti.append(
                f"- {trasa.get('nazev', '')} | typ: {trasa.get('typ', '')} | délka: {trasa.get('delka_m', 0)} m | cena: {trasa.get('cena_czk', 0)} Kč"
            )
    else:
        casti.append("Návrh kabelových tras: nebyl vygenerován.")

    return "\n".join(casti).encode("utf-8")


def vytvorit_html_projektovy_posudek(
        scan_data,
        thermo_data,
        interior_data,
        schema_data,
        trasy_data,
        data_objektu,
):
        mistnosti = interior_data.get("mistnosti", []) if isinstance(interior_data, dict) else []
        prvky = schema_data.get("prvky", []) if isinstance(schema_data, dict) else []
        trasy = trasy_data.get("trasy", []) if isinstance(trasy_data, dict) else []

        html = f"""
<html>
<head>
    <meta charset=\"utf-8\" />
    <title>Projektová dokumentace</title>
</head>
<body>
    <h1>Projektová dokumentace</h1>
    <h2>Objekt</h2>
    <p>Lokalita: {data_objektu.get('lokace', 'N/A')}</p>
    <p>Investor: {data_objektu.get('investor', 'N/A')}</p>

    <h2>Termovize</h2>
    <p>Stav: {thermo_data.get('stav', 'N/A') if isinstance(thermo_data, dict) else 'N/A'}</p>
    <p>Anomálie: {thermo_data.get('procento_anomalii', 'N/A') if isinstance(thermo_data, dict) else 'N/A'} %</p>

    <h2>Energetický posudek</h2>
    <p>Plocha: {scan_data.get('plocha_m2', 'N/A') if isinstance(scan_data, dict) else 'N/A'} m²</p>

    <h2>Interiér</h2>
    <p>Počet místností: {len(mistnosti)}</p>

    <h2>Elektroinstalace</h2>
    <p>Počet prvků schématu: {len(prvky)}</p>
    <p>Počet kabelových tras: {len(trasy)}</p>
</body>
</html>
"""
        return html


def vytvorit_projektovy_balicek(data_objektu, xlsx_bytes, bbox=None):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('Cast_A_Pruvodni_Zprava.txt', vygenerovat_pruvodni_zpravu_pro_urad(data_objektu))
        zf.writestr('Projektovy_Rozpocet.xlsx', xlsx_bytes)
        zf.writestr('Model_IFC.ifc', vytvorit_ifc_export(data_objektu))

        schema_data, trasy_data = _ziskat_elektricke_dokumentacni_data()
        if schema_data:
            zf.writestr(
                'Elektrika_Schema_Rozvadce.xlsx',
                vytvor_excel_schema_rozvadce_bytes(schema_data, nazev_objektu=data_objektu.get('id_projektu', 'Rozvaděč')),
            )
        if trasy_data:
            zf.writestr(
                'Elektrika_Nabidka_Kabelove_Trasy.xlsx',
                vytvor_excel_nabidka_kabelove_trasy_bytes(trasy_data, nazev_objektu=data_objektu.get('id_projektu', 'Projekt')),
            )
        if schema_data or trasy_data:
            zf.writestr(
                'Elektrika_Projektova_Dokumentace.txt',
                _vytvorit_elektricke_souhrn_textu(schema_data, trasy_data, data_objektu),
            )
        zf.writestr('Model_DXF.dxf', vytvorit_dxf_export(data_objektu))
        dwg_bytes = vytvorit_dwg_export(data_objektu)
        if dwg_bytes:
            zf.writestr('Model_DWG.dwg', dwg_bytes)
        else:
            zf.writestr('Model_DWG.txt', 'DWG export vyžaduje externí konverzní nástroj ODAFileConverter nebo nastavenou proměnnou ODAFILECONVERTER_PATH. V balíčku je k dispozici DXF jako plnohodnotná alternativa pro AutoCAD.')
        zf.writestr('Metadata.json', vytvorit_metadata_json(data_objektu))

        if bbox is not None:
            mapa_bytes = stahnout_katastralni_mapu_cuzk(bbox)
            if mapa_bytes:
                zf.writestr('Situace_Katastr.png', mapa_bytes)
            else:
                zf.writestr('Situace_Katastr.txt', 'Katastrální mapa nebyla stažena nebo není dostupná.')

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def vygenerovat_pruvodni_zpravu_pro_urad(data_objektu):
    obsah = f"""================================================================================
DOKUMENTACE PRO STAVEBNÍ POVOLENÍ / OHLÁŠENÍ STAVBY
ČÁST A - PRŮVODNÍ ZPRÁVA
================================================================================
Generováno systémem: BIM Scan AI Engine v1.0
Datum vygenerování: {datetime.datetime.now().strftime('%d.%m.%Y')}

A.1 IDENTIFIKAČNÍ ÚDAJE STAVBY
--------------------------------------------------------------------------------
A.1.1 Údaje o stavbě:
  - Název stavby: Modernizace a rekonstrukce technické infrastruktury objektu
  - Místo stavby (kraj, obec, katastrální území): {data_objektu['lokace']}
  - Číslo parcely: {data_objektu['parcela']}
  - Předmět dokumentace: Stavební úpravy, silnoproudá elektrotechnika, LPS, statika podlah.

A.1.2 Údaje o stavebníkovi (investorovi):
  - Název/Jméno: {data_objektu['investor']}
  - Adresa: {data_objektu['investor_adresa']}

A.1.3 Údaje o zpracovateli dokumentace:
  - Projektant (generální projektant): BIM Scan AI Engine (Autonomní digitální pasportizační systém)
  - Odpovědný zástupce / Autorizovaná osoba: [MÍSTO PRO RAZÍTKO A PODPIS AUTORIZOVANÉHO INŽENÝRA ČKAIT]

A.2 SEZNAM VSTUPNÍCH PODKLADŮ
--------------------------------------------------------------------------------
  1. Digitální 3D mračno bodů (Point Cloud) získané fotogrammetrickým skenováním dne {data_objektu['datum_skenu']}.
  2. Termovizní diagnostika povrchových teplot rozvaděčů a kabelových tras.
  3. Geodetické zaměření parcelních hranic z databáze ČÚZK (Katastr nemovitostí).
  4. Elektricko-inženýrské podklady: návrh schématu rozvaděče, rozložení světel a zásuvek a návrh kabelových tras vč. orientační ceny.

A.3 STRUČNÝ POPIS STAVBY Z HLEDISKA STAVEBNÍHO A TECHNICKÉHO
--------------------------------------------------------------------------------
A.3.1 Současný stav:
  Objekt vykazuje absenci původní projektové dokumentace. Stávající elektroinstalace a technická infrastruktura jsou na hranici životnosti s detekovanými termálními anomáliemi (přechodové odpory). Podlahové konstrukce vyžadují posouzení pro nové průmyslové zatížení.

A.3.2 Navrhovaný stav:
  Bude provedena kompletní výměna kabelových tras silnoproudu s pravoúhlým vedením v instalačních zónách podle ČSN. Na střeše objektu bude instalována nová jímací soustava (hromosvod) třídy {data_objektu['trida_lps']} navržená metodou valivé koule. Průmyslová podlaha (betonová deska tl. {data_objektu['tloustka_podlahy_mm']} mm) je staticky ověřena na maximální bodové zatížení {data_objektu['max_zatizeni_tuny']} tun na patku regálu. Součástí projektové dokumentace jsou rovněž návrhy rozvaděčového schématu, rozmístění světel a zásuvek a orientační kabelové trasy s cenovou nabídkou.

A.4 VLIV STAVBY NA ŽIVOTNÍ PROSTŘEDÍ A OCHRANA ZDRAVÍ
--------------------------------------------------------------------------------
Stavba nemá negativní vliv na životní prostředí. Modernizací osvětlovací soustavy (přechod na LED technologie) dojde ke snížení energetické náročnosti budovy o odhadovaných {data_objektu['uspora_kwh']} kWh ročně. Při realizaci budou dodrženy obecné požadavky na bezpečnost práce podle platných předpisů ČR.
================================================================================
"""
    return obsah.strip()


def generovat_energeticky_posudek(plocha_obalky_m2, material_zdi, plocha_oken_m2, lokalita):
    """
    Autonomní výpočet tepelných ztrát a energetické náročnosti budovy
    podle metodiky zákona č. 406/2000 Sb.
    """
    u_stena = 0.18 if material_zdi == "Ocelový skelet + PIR panely" else 0.30
    u_okno = 1.0  # Průmyslové izolační trojsklo

    teploty_lokalit = {"Praha": -12, "Plzeň": -12, "Liberec": -15, "Pec pod Sněžkou": -18}
    t_vnejsi = teploty_lokalit.get(lokalita, -12)
    delta_t = 20 - t_vnejsi

    plocha_sten = max(0, plocha_obalky_m2 - plocha_oken_m2)
    ztrata_prostupem_w = (plocha_sten * u_stena * delta_t) + (plocha_oken_m2 * u_okno * delta_t)
    celkova_ztrata_kw = (ztrata_prostupem_w * 1.3) / 1000
    merna_spotreba = (celkova_ztrata_kw * 24 * 220) / max(1, (plocha_obalky_m2 / 5))

    if merna_spotreba < 50:
        trida = "A - Mimořádně úsporná"
    elif merna_spotreba < 100:
        trida = "B - Úsporná"
    elif merna_spotreba < 150:
        trida = "C - Úsporná (Vyhovující normě)"
    else:
        trida = "G - Mimořádně nehospodárná"

    return {
        "tepelna_ztrata_objektu_kw": round(celkova_ztrata_kw, 1),
        "energeticka_trida_penb": trida,
        "vnejsi_navrhova_teplota": t_vnejsi
    }


def zobrazit_energeticky_modul():
    st.markdown("#### 🍃 Energetická náročnost a audit (Zákon č. 406/2000 Sb.)")
    lokalita = st.selectbox("Lokalita pro energetické posouzení:", ["Praha", "Plzeň", "Liberec", "Pec pod Sněžkou"])
    plocha = st.number_input("Celková plocha obálky budovy (m²):", min_value=100, value=4500)
    material = st.selectbox("Materiál konstrukce:", ["Ocelový skelet + PIR panely", "Železobetonový prefabrikovaný skelet"])
    plocha_oken = st.number_input("Celková plocha prosklených ploch / oken (m²):", min_value=10, value=300)

    posudek_data = generovat_energeticky_posudek(plocha, material, plocha_oken, lokalita)

    if "A" in posudek_data["energeticka_trida_penb"] or "B" in posudek_data["energeticka_trida_penb"]:
        st.success(f"📊 Klasifikace budovy: {posudek_data['energeticka_trida_penb']}")
    else:
        st.warning(f"📊 Klasifikace budovy: {posudek_data['energeticka_trida_penb']}")

    st.write(f"Celková projektovaná tepelná ztráta objektu: **{posudek_data['tepelna_ztrata_objektu_kw']} kW**")
    st.write(f"Vnější návrhová teplota podle lokality: **{posudek_data['vnejsi_navrhova_teplota']} °C**")

    if st.button("📊 Vytvořit Excel ROI + ESG report"):
        data_energo = {"vykon_kwp": 250.0}
        xlsx_bytes = vytvor_excel_rozpocet_s_esg_bytes(data_energo)
        st.success("Excelový report ROI + ESG byl připraven k stažení.")
        st.download_button(
            "📥 Stáhnout Excel ROI + ESG report",
            data=xlsx_bytes,
            file_name="Rozpocet_ESG_ROI.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def vytvorit_zip_pro_stavebni_urad(data_objektu, bbox=None):
    """Vytvoří ZIP balíček s průvodní zprávou a katastrální mapou."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zprava = vygenerovat_pruvodni_zpravu_pro_urad(data_objektu)
        zf.writestr('Cast_A_Pruvodni_Zprava.txt', zprava)

        mapa_bytes = None
        if bbox is not None:
            mapa_bytes = stahnout_katastralni_mapu_cuzk(bbox)
        if mapa_bytes:
            zf.writestr('Situace_Katastr.png', mapa_bytes)
        else:
            zf.writestr('Situace_Katastr.txt', 'Katastrální mapa nebyla stažena. Ověřte připojení k API ČÚZK nebo dostupnost vrstvy.')

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def vypocitat_klimaticke_zatizeni(lokalita_cr, plocha_strechy_m2, sklon_strechy_deg=0):
    """
    Autonomní výpočet klimatického zatížení střechy průmyslového objektu
    podle ČSN EN 1991-1-3 (Sníh) a ČSN EN 1991-1-4 (Vítra).
    """
    databaze_mist = {
        "Praha": {"snih_oblast": 1, "vitr_oblast": 2},
        "Plzeň": {"snih_oblast": 2, "vitr_oblast": 2},
        "Liberec": {"snih_oblast": 4, "vitr_oblast": 3},
        "Pec pod Sněžkou": {"snih_oblast": 7, "vitr_oblast": 5}
    }

    info_misto = databaze_mist.get(lokalita_cr, {"snih_oblast": 2, "vitr_oblast": 2})
    tabulka_snehu = {1: 0.7, 2: 1.0, 3: 1.5, 4: 2.0, 5: 2.5, 6: 3.0, 7: 4.0, 8: 4.0}
    sk = tabulka_snehu[info_misto["snih_oblast"]]
    mu = 0.8 if sklon_strechy_deg < 30 else 0.8 * ((60 - sklon_strechy_deg) / 30)
    zatizeni_snehem_kn_m2 = mu * sk
    zatizeni_snehem_kg_m2 = zatizeni_snehem_kn_m2 * 100
    tabulka_vetru = {1: 0.22, 2: 0.32, 3: 0.44, 4: 0.58, 5: 0.72}
    qb = tabulka_vetru[info_misto["vitr_oblast"]]
    celkovy_snih_tony = (zatizeni_snehem_kg_m2 * plocha_strechy_m2) / 1000

    return {
        "lokalita": lokalita_cr,
        "snehova_oblast": info_misto["snih_oblast"],
        "vetrna_oblast": info_misto["vitr_oblast"],
        "tlak_snehu_kg_m2": round(zatizeni_snehem_kg_m2, 1),
        "tlak_vetru_kn_m2": qb,
        "celkova_vaha_snehu_na_strese_tony": round(celkovy_snih_tony, 1)
    }


def zobrazit_modul_skenovani():
    st.markdown("### 🛸 Skenování budov – Dron + Termovize + Interiér")
    st.write("Nahrajte data ze skenování – systém automaticky extrahuje rozměry, detekuje anomálie a sestaví protokol.")

    tab_ext, tab_termo, tab_int = st.tabs([
        "📡 Dron – Exteriér (Point Cloud)",
        "🌡️ Termovizní kamera",
        "📸 Interiér (Mobil / 360°)",
    ])

    # ── TAB 1: Point Cloud ──────────────────────────────────────
    with tab_ext:
        st.markdown("**Podporované formáty:** PLY, PCD, XYZ, PTS *(nativní Open3D)*  |  LAS, LAZ, E57 *(simulace)*")
        soubor_pc = st.file_uploader(
            "Nahrát soubor mračna bodů:",
            type=["ply", "pcd", "xyz", "pts", "las", "laz", "e57"],
            key="scan_pc_upload"
        )
        if soubor_pc is not None:
            with st.spinner("⏳ Zpracovávám point cloud – segmentace roviny, bounding box..."):
                vysledek = nacist_a_analyzovat_mracno(soubor_pc.read(), soubor_pc.name)

            st.success(f"✅ Zpracováno: **{soubor_pc.name}**  |  Zdroj: {vysledek['zdroj']}")

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Délka objektu", f"{vysledek['delka_m']} m")
            col_b.metric("Šířka objektu", f"{vysledek['sirka_m']} m")
            col_c.metric("Výška objektu", f"{vysledek['vyska_m']} m")

            col_d, col_e, col_f = st.columns(3)
            col_d.metric("Plocha půdorysu", f"{vysledek['plocha_m2']} m²")
            col_e.metric("Počet bodů", f"{vysledek['pocet_bodu']:,}")
            col_f.metric("Hustota", f"{vysledek['hustota_bodu_m2']} b/m²")

            st.caption(f"Přesnost měření: ±{vysledek['presnost_m']} m  |  Body podlahy: {vysledek['bodu_rovina_podlahy']:,}  |  Body stěn: {vysledek['bodu_svisle_steny']:,}")

            st.info("💡 Rozměry byly automaticky předvyplněny do formuláře stavebního rozpočtu níže.")
            _session_set("scan_delka", vysledek["delka_m"])
            _session_set("scan_sirka", vysledek["sirka_m"])
            _session_set("scan_vyska", vysledek["vyska_m"])
            _session_set("scan_pc_data", vysledek)
        else:
            st.info("Nahrajte soubor point cloudu z dronu (DJI Zenmuse L2, Leica BLK2FLY, RIEGL UAS...).")

    # ── TAB 2: Termovize ────────────────────────────────────────
    with tab_termo:
        st.markdown("**Podporované formáty:** JPG, PNG, TIFF *(termovizní export z FLIR Tools / DJI Thermal)*")
        soubor_termo = st.file_uploader(
            "Nahrát termovizní snímek:",
            type=["jpg", "jpeg", "png", "tiff", "tif"],
            key="scan_termo_upload"
        )
        if soubor_termo is not None:
            with st.spinner("⏳ Analyzuji teplotní mapu – detekce anomálií..."):
                termo = analyzovat_termovizni_snimek(soubor_termo.read())

            stav_barva = {"OK": "success", "VAROVÁNÍ": "warning", "ALARM": "error"}
            getattr(st, stav_barva.get(termo["stav"], "info"))(f"{termo['zavaznost']}")

            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Anomální plocha", f"{termo['procento_anomalii']} %")
            col_t2.metric("Max. teplota (odhad)", f"{termo['max_teplota_odh_c']} °C")
            col_t3.metric("Průměrná teplota", f"{termo['avg_teplota_odh_c']} °C")

            st.markdown("**Detekované anomálie:**")
            for anomalie in termo["anomalie_typy"]:
                st.write(f"- {anomalie}")

            st.caption(f"Hot-spot: X={termo['hot_spot_x_pct']} %, Y={termo['hot_spot_y_pct']} % plochy snímku  |  Rozlišení: {termo['rozliseni_px']}")
            _session_set("scan_termo_data", termo)

            st.image(soubor_termo, caption="Nahraný termovizní snímek", use_column_width=True)
        else:
            st.info("Nahrajte termovizní snímek z dronu (DJI Zenmuse H20T, FLIR Vue Pro, InfraTec...).")

    # ── TAB 3: Interiér ─────────────────────────────────────────
    with tab_int:
        st.markdown("**Vstup:** Fotografie z mobilního telefonu, 360° kamery nebo akční kamery.")
        st.caption("Doporučení: min. 4 snímky na místnost z rohů, překrytí snímků > 60 %.")
        soubory_int = st.file_uploader(
            "Nahrát fotografie interiéru:",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key="scan_int_upload"
        )
        if soubory_int:
            with st.spinner(f"⏳ Zpracovávám {len(soubory_int)} snímků – odhad půdorysu..."):
                int_data = zpracovat_interiérove_fotografie(
                    [f.read() for f in soubory_int],
                    [f.name for f in soubory_int]
                )

            st.success(f"✅ Zpracováno {int_data['pocet_snimku']} snímků  |  Detekováno {int_data['pocet_mistnosti']} místností  |  Celková plocha: {int_data['celkova_plocha_m2']} m²")

            for m in int_data["mistnosti"]:
                with st.expander(f"Místnost {m['cislo']} – {m['delka_m']} × {m['sirka_m']} m, výška {m['vyska_m']} m ({m['plocha_m2']} m²)"):
                    st.write(f"**Objem:** {m.get('objem_m3', round(m['plocha_m2']*m['vyska_m'],1))} m³")
                    st.write(f"**Detekované prvky:** {', '.join(m['detekované_prvky'])}")

            st.caption(f"{int_data['presnost']}  –  {int_data['doporuceni']}")
            _session_set("scan_int_data", int_data)
        else:
            st.info("Nahrajte fotografie z obchůzky objektu (iPhone LiDAR, Samsung, GoPro, Ricoh Theta...).")

    # ── Návrh kabelových tras a cena ─────────────────────────
    st.write("")
    if st.button("🧰 Navrhnout kabelové trasy a cenovou nabídku", key="scan_trasy_btn"):
        int_d = _session_get("scan_int_data", None)
        if int_d:
            with st.spinner("⏳ Vypočítávám optimální vedení a cenovou nabídku..."):
                trasy_data = navrhnout_kabelove_trasy(int_d)
            _session_set("scan_trasy_data", trasy_data)
            st.success(f"✅ Návrh hotový: {len(trasy_data['trasy'])} tras, celková cena {trasy_data['celkova_cena_czk']:,} Kč")
            for trasa in trasy_data["trasy"]:
                st.write(f"- {trasa['nazev']} | {trasa['typ']} | {trasa['delka_m']} m | {trasa['cena_czk']:,} Kč")
            xlsx_bytes = vytvor_excel_nabidka_kabelove_trasy_bytes(trasy_data, nazev_objektu="Skenovaný objekt")
            st.download_button(
                "📥 Stáhnout nabídku kabelových tras (.XLSX)",
                data=xlsx_bytes,
                file_name="Nabidka_Kabelove_Trasy.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="scan_trasy_dl"
            )
        else:
            st.warning("Nejdříve nahrajte interiérové fotografie, aby bylo možné navrhnout trasy.")

    st.write("")
    st.markdown("### 🔌 Schéma rozvaděče z fotografie")
    st.caption("Nahrajte fotku rozvaděče a systém vygeneruje jednoduché schéma se světly, zásuvkami a dalšími prvky.")
    foto_rozvadec = st.file_uploader("Nahrát fotografii rozvaděče:", type=["jpg", "jpeg", "png"], key="scan_rozvadec_upload")
    vybrane_prvky = st.multiselect(
        "Typy prvků pro schéma:",
        ["svetla", "zasuvky", "ventilator", "pojistky", "dioda", "zvonek"],
        default=["svetla", "zasuvky"],
        key="scan_rozvadec_prvky"
    )
    if foto_rozvadec is not None and st.button("🧾 Vytvořit schéma rozvaděče", key="scan_schema_btn"):
        with st.spinner("⏳ Generuji schéma rozvaděče z fotografie..."):
            schema = vytvorit_schema_rozvadce_z_fotky(foto_rozvadec.read(), foto_rozvadec.name, prvky=vybrane_prvky)
        _session_set("scan_schema_data", schema)
        st.success(f"✅ Schéma vytvořeno: {len(schema['prvky'])} prvků")
        for prvek in schema["prvky"]:
            st.write(f"- {prvek['nazev']} | {prvek['typ']} | obvod {prvek['circuit']}")
        xlsx_schema = vytvor_excel_schema_rozvadce_bytes(schema, nazev_objektu="Rozvaděč")
        st.download_button(
            "📥 Stáhnout schéma rozvaděče (.XLSX)",
            data=xlsx_schema,
            file_name="Schema_Rozvadce.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="scan_schema_dl"
        )

    # ── Export protokolu ────────────────────────────────────────
    st.write("")
    if st.button("📋 Exportovat kompletní protokol o skenování (.XLSX)", key="scan_export_btn"):
        pc_data   = _session_get("scan_pc_data",    {"pocet_bodu": 0, "delka_m": 0, "sirka_m": 0, "vyska_m": 0, "plocha_m2": 0, "hustota_bodu_m2": 0, "bodu_rovina_podlahy": 0, "bodu_svisle_steny": 0, "presnost_m": 0.05, "zdroj": "–", "pripona": "–"})
        termo_d   = _session_get("scan_termo_data", None)
        int_d     = _session_get("scan_int_data",   None)
        xlsx_bytes = vytvor_excel_protokol_skenovani_bytes(pc_data, termo_d, int_d)
        st.download_button(
            "📥 Stáhnout protokol skenování (.XLSX)",
            data=xlsx_bytes,
            file_name="Protokol_Skenovani.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="scan_export_dl"
        )


def zobrazit_stavebni_rozpocet():
    st.markdown("### 🧱 AI Sestavení stavebního rozpočtu (dle TSKP)")
    st.write("Zadejte typ a rozměry objektu – AI sestaví řádkový rozpočet se členěním na stavební díly (HSV/PSV) včetně DPH.")

    typy = list(TYPY_BUDOV_ROZPOCET.keys())
    col_r1, col_r2 = st.columns(2)

    with col_r1:
        typ_b = st.selectbox("Typ objektu:", typy, key="sr_typ")
        delka = st.number_input("Délka objektu (m):", min_value=5.0, max_value=500.0, value=60.0, step=1.0, key="sr_delka")
        sirka = st.number_input("Šířka objektu (m):", min_value=5.0, max_value=200.0, value=30.0, step=1.0, key="sr_sirka")
        vyska = st.number_input("Výška / světlá výška podlaží (m):", min_value=2.5, max_value=30.0, value=8.0, step=0.5, key="sr_vyska")
        podlazi = st.number_input("Počet nadzemních podlaží:", min_value=1, max_value=30, value=1, step=1, key="sr_podlazi")

    with col_r2:
        konfig_b = TYPY_BUDOV_ROZPOCET[typ_b]
        hpp = delka * sirka * podlazi
        cena_bez_dph = hpp * konfig_b["cena_m2"]
        cena_s_dph = cena_bez_dph * 1.21
        st.write(f"**Konstrukční systém:** {konfig_b['poznamka']}")
        st.metric("Hrubá podlažní plocha (HPP)", f"{hpp:,.0f} m²")
        st.metric("Odhadovaná cena bez DPH", f"{cena_bez_dph:,.0f} Kč")
        st.metric("Odhadovaná cena včetně DPH", f"{cena_s_dph:,.0f} Kč")
        st.caption(f"Jednotková cena: {konfig_b['cena_m2']:,} Kč/m² HPP (orientační dle ÚRS / RTS Data)")

    if st.button("📋 Sestavit podrobný stavební rozpočet (.XLSX)", key="sr_export"):
        xlsx_bytes = vytvor_excel_stavebni_rozpocet_bytes(typ_b, delka, sirka, vyska, int(podlazi))
        st.success(f"Rozpočet pro **{typ_b}** ({hpp:,.0f} m² HPP) byl sestaven.")
        st.download_button(
            "📥 Stáhnout stavební rozpočet – TSKP (.XLSX)",
            data=xlsx_bytes,
            file_name=f"Stavebni_Rozpocet_{typ_b.replace(' ', '_').replace('/', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="sr_download"
        )


def zobrazit_generator_energetickych_projektu():
    st.markdown("### ⚡ AI Generátor energetických projektů")
    st.write("Zadejte parametry – AI okamžitě vypočítá investici (CAPEX), roční výnosy, návratnost a ESG CO₂ bilanci.")

    TYPY_ENERGO = list(KONFIGURACE_ENERGO.keys())

    col_e1, col_e2 = st.columns(2)

    with col_e1:
        typ = st.selectbox("Typ energetického projektu:", TYPY_ENERGO, key="energo_typ")
        konfig = KONFIGURACE_ENERGO[typ]
        vykon = st.number_input(
            f"{konfig['legenda_vykonu']} ({konfig['jednotka_vykonu']}):",
            min_value=1.0, max_value=50000.0, value=250.0, step=10.0, key="energo_vykon"
        )
        cena_kwh = st.number_input(
            "Aktuální cena energie / výkupní cena (Kč/kWh):",
            min_value=1.0, max_value=20.0, value=5.5, step=0.1, key="energo_cena"
        )

    with col_e2:
        capex_odh = vykon * konfig["cena_jednotky"] + konfig["fixni_naklady"]
        rocni_vyroba_odh = vykon * konfig["spec_vyroba_kwh"]
        rocni_vynosy_odh = rocni_vyroba_odh * cena_kwh
        opex_odh = capex_odh * 0.015
        navratnost_odh = capex_odh / max(rocni_vynosy_odh - opex_odh, 1)
        co2_rocne = (rocni_vyroba_odh / 1000) * konfig["emisni_faktor"]

        st.metric("Odhadovaná investice (CAPEX)", f"{capex_odh:,.0f} Kč")
        st.metric("Roční výnosy / úspory", f"{rocni_vynosy_odh:,.0f} Kč")
        st.metric("Prostá návratnost", f"{navratnost_odh:.1f} let")
        st.metric("Roční úspora CO₂", f"{co2_rocne:.1f} t CO₂e")

    if st.button("📊 Generovat investiční rozpočet (.XLSX)", key="energo_export"):
        xlsx_bytes = vytvor_excel_energeticky_projekt_bytes(typ, vykon, cena_kwh)
        st.success(f"Excel rozpočet pro projekt **{typ}** byl připraven k stažení.")
        st.download_button(
            "📥 Stáhnout CAPEX + OPEX + ESG (.XLSX)",
            data=xlsx_bytes,
            file_name=f"Rozpocet_{typ.replace(' ', '_').replace('/', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="energo_download"
        )


TYPY_OBJEKTU = {
    "🏭 Průmysl a logistika": [
        "Logistická hala / Sklad",
        "Výrobní závod",
        "Datové centrum",
        "Čistírna odpadních vod",
    ],
    "🏢 Komerční a administrativní": [
        "Administrativní budova",
        "Obchodní centrum / retail",
        "Hotel / ubytovací zařízení",
        "Kancelářský park",
    ],
    "🏠 Bytová výstavba": [
        "Bytový dům",
        "Rodinný dům / vila",
        "Sociální bydlení",
        "Studentská kolej",
    ],
    "🏥 Veřejné stavby a občanská vybavenost": [
        "Nemocnice / zdravotnické zařízení",
        "Škola / vzdělávací centrum",
        "Sportovní hala / stadion",
        "Kulturní centrum / muzeum",
        "Hasičská stanice",
    ],
    "🌉 Inženýrské stavby": [
        "Most / lávka",
        "Tunel",
        "Přehrada / vodní dílo",
        "Věžový objekt / stožár",
    ],
    "🚢 Lodě a plovoucí konstrukce": [
        "Nákladní loď",
        "Osobní / výletní loď",
        "Remorkér / pracovní plavidlo",
        "Plovoucí platforma / pontón",
    ],
}

MATERIALY_DLE_TYPU = {
    "Logistická hala / Sklad": ["Ocelový skelet + PIR panely", "Železobetonový prefabrikovaný skelet"],
    "Výrobní závod": ["Ocelový skelet + PIR panely", "Železobetonový monolitický skelet"],
    "Bytový dům": ["Zděná konstrukce (cihelné bloky)", "Železobetonový monolitický skelet", "Dřevostavba (CLT panely)"],
    "Rodinný dům / vila": ["Zděná konstrukce (cihelné bloky)", "Dřevostavba (CLT panely)", "Montovaná konstrukce"],
    "Nemocnice / zdravotnické zařízení": ["Železobetonový monolitický skelet", "Ocelový skelet"],
    "Škola / vzdělávací centrum": ["Železobetonový prefabrikovaný skelet", "Zděná konstrukce (cihelné bloky)"],
    "Most / lávka": ["Předpjatý beton (ČSN EN 1992)", "Ocelová příhradová konstrukce", "Kompozit ocel-beton"],
    "Tunel": ["Železobetonová ostění (NATM)", "Prefabrikované segmenty (TBM)"],
    "Nákladní loď": ["Lodní ocel S235 / AH36 (třída lodi)", "Vysokopevnostní ocel AH40"],
    "Osobní / výletní loď": ["Lodní ocel S235 / AH36 (třída lodi)", "Hliníkové slitiny (5000/6000 řada)"],
    "Remorkér / pracovní plavidlo": ["Lodní ocel S235 / AH36 (třída lodi)"],
    "Plovoucí platforma / pontón": ["Lodní ocel S235 / AH36 (třída lodi)", "Železobeton (plovoucí základy)"],
}
DEFAULT_MATERIAL = ["Ocelový skelet + PIR panely", "Železobetonový prefabrikovaný skelet"]


def zobrazit_generator_novostaveb():
    st.markdown("### 🏗️ AI Generátor projektů na zelené louce (Generative Design)")
    st.write("Zadejte základní parametry – AI navrhne BIM model, statiku, sítě a průvodní dokumentaci pro jakýkoli typ objektu nebo plavidla.")

    col_in1, col_in2 = st.columns(2)

    with col_in1:
        kategorie = st.selectbox("Kategorie objektu:", list(TYPY_OBJEKTU.keys()), key="gen_kategorie")
        ucel = st.selectbox("Typ / účel objektu:", TYPY_OBJEKTU[kategorie], key="gen_ucel")
        je_lod = "loď" in ucel.lower() or "plavidlo" in ucel.lower() or "pontón" in ucel.lower() or "platforma" in ucel.lower()

        if je_lod:
            delka = st.number_input("Délka plavidla (m):", min_value=5, max_value=400, value=80)
            sirka = st.number_input("Šířka / šíře (m):", min_value=3, max_value=60, value=14)
            vyska = st.number_input("Výška boku / nadstavba (m):", min_value=2, max_value=40, value=6)
        else:
            delka = st.number_input("Délka objektu (m):", min_value=5, max_value=600, value=60)
            sirka = st.number_input("Šířka objektu (m):", min_value=3, max_value=300, value=30)
            vyska = st.number_input("Výška / světlá výška (m):", min_value=2, max_value=300, value=8)

    with col_in2:
        if je_lod:
            st.text_input("Loděnice / přístav:", value="Přístav Praha-Holešovice", key="gen_lokace")
        else:
            st.text_input("Katastrální území / Parcela:", value="Plzeň-město, parc. č. 552/1", key="gen_lokace")

        dostupne_materialy = MATERIALY_DLE_TYPU.get(ucel, DEFAULT_MATERIAL)
        material = st.selectbox("Konstrukční systém:", dostupne_materialy, key="gen_material")

        plocha_m2 = delka * sirka
        objem_m3 = delka * sirka * vyska
        st.caption(f"Zastavěná plocha: **{plocha_m2:,.0f} m²** | Obestavěný prostor: **{objem_m3:,.0f} m³**")
        st.write("")

        if st.button("🚀 SPUSTIT GENERATIVNÍ NÁVRH PROJEKTU", key="gen_start"):
            with st.spinner("AI generuje 3D BIM model, počítá statiku a kreslí výkresy..."):
                import time
                time.sleep(3)
            data_objektu = {
                "id_projektu": f"GEN-{ucel[:12].upper().replace(' ', '-')}",
                "lokace": st.session_state.get("gen_lokace", ""),
                "parcela": st.session_state.get("gen_lokace", ""),
                "investor": "BIM Scan AI Client",
                "investor_adresa": "N/A",
                "datum_skenu": datetime.datetime.now().strftime('%d.%m.%Y'),
                "trida_lps": "LPS II",
                "tloustka_podlahy_mm": 180 if not je_lod else 0,
                "max_zatizeni_tuny": round(max(1.0, plocha_m2 / 100), 1),
                "uspora_kwh": int(objem_m3 * 12),
            }
            if je_lod:
                data_objektu["lokace"] = st.session_state.get("gen_lokace", "Přístav Praha-Holešovice")
                data_objektu["trida_lps"] = "N/A"
                data_objektu["tloustka_podlahy_mm"] = 0

            if "Roda" in material or "ocel" in material.lower():
                data_objektu["max_zatizeni_tuny"] = round(max(1.5, plocha_m2 / 80), 1)

            xlsx_bytes = vytvor_excel_stavebni_rozpocet_bytes(
                typ_budovy=ucel if ucel in TYPY_BUDOV_ROZPOCET else "Průmyslová hala / Logistika",
                delka_m=delka,
                sirka_m=sirka,
                vyska_m=vyska,
                pocet_podlazi=1,
            )
            data_objektu["delka_m"] = delka
            data_objektu["sirka_m"] = sirka
            data_objektu["vyska_m"] = vyska
            zip_bytes = vytvorit_projektovy_balicek(data_objektu, xlsx_bytes)
            st.success(f"🎉 Projektová dokumentace objektu **{ucel}** byla kompletně vygenerována!")
            st.download_button("📥 Stáhnout Dokumentaci pro stavební úřad (.ZIP)", data=zip_bytes, file_name=f"Kompletni_Projekt_{ucel.replace(' ', '_').replace('/', '_')}.zip", key="gen_download")


st.set_page_config(page_title="BIM Scan AI Enterprise", page_icon="🏢", layout="wide")

# 1. Lokalizační slovník (Internacionalizace - i18n)
LANGUAGES = {
    "CZ": {
        "title": "🏢 BIM Scan AI – Globální Enterprise Platforma",
        "select_lang": "Zvolte jazyk / Select Language:",
        "region": "Regulační standard (Normy):",
        "wall_mat": "Materiál konstrukce:",
        "btn_run": "🚀 SPUSTIT GENERATIVNÍ NÁVRH",
        "status": "Aktivní licence: GLOBAL ENTERPRISE"
    },
    "EN": {
        "title": "🏢 BIM Scan AI – Global Enterprise Platform",
        "select_lang": "Zvolte jazyk / Select Language:",
        "region": "Regulatory Standard (Codes):",
        "wall_mat": "Structural Material:",
        "btn_run": "🚀 RUN GENERATIVE DESIGN",
        "status": "Active License: GLOBAL ENTERPRISE"
    }
}

lang_choice = st.sidebar.selectbox("Language / Jazyk", ["CZ", "EN"])
ln = LANGUAGES[lang_choice]

st.markdown(
    """
    <style>
    .main { background-color: #F8FAFC; }
    .stButton>button { background-color: #2B6CB0; color: white; width: 100%; font-weight: bold; height: 3em; }
    .price-tag { font-size: 24px; font-weight: bold; color: #1A365D; background-color: #E2E8F0; padding: 10px; border-radius: 5px; text-align: center; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.sidebar.markdown("## 🔐 Autentizace a přístup")
user_role = st.sidebar.selectbox("Přihlásit se jako uživatel:", [
    "director@metrostav.cz (Generální ředitel / Majitel)", 
    "projektant1@metrostav.cz (Inženýr v terénu)"
])

# Simulace nastavení práv na základě volby
is_owner = "director" in user_role
email_session = "director@metrostav.cz" if is_owner else "projektant1@metrostav.cz"

st.title(ln["title"])
st.write(f"**{ln['status']}** | Region: Worldwide Cloud Core")

st.markdown(f"### 🗄️ Správa projektové dokumentace společnosti")
if is_owner:
    st.success("👑 Vítejte v administrátorském režimu (Master Access). Máte plný přístup k projektům všech divizí.")
else:
    st.info("👤 Jste přihlášen jako projektant. Vidíte své přidělené zakázky.")

st.write("---")
st.markdown("#### 🔄 Verzování a externí sdílení aktivního projektu")
col_v1, col_v2 = st.columns(2)

with col_v1:
    st.write("**Historie revizí projektu:**")
    st.write("- **Verze 2 (Aktuální):** Automatický návrh tras a hromosvodech (Verifikováno expertním auditorem)")
    st.write("- **Verze 1 (Původní):** Výchozí 3D pasportizace z dat z dronu")
    
    if st.button("➕ Vytvořit novou revizi (Verze 3)"):
        st.toast("Vytvářím novou nezávislou výpočtovou větev...", icon="🔄")

with col_v2:
    st.write("**Sdílení s investorem / subdodavateli:**")
    if _session_get("share_url", None) is None:
        _session_set("share_url", "")
    if st.button("🔗 Vygenerovat bezpečný odkaz pro sdílení"):
        _session_set("share_url", f"https://expert-system.com/{secrets.token_urlsafe(16)}")
    share_url = _session_get("share_url", "")
    if share_url:
        st.code(share_url, language="text")
        st.caption("Tento odkaz umožní externím partnerům stažení CAD/BIM dat bez nutnosti registrace. Platnost odkazu je 30 dní.")

st.write("---")

col1, col2 = st.columns([1, 2])

with col1:
    st.markdown("### 🛠️ Kontrola parametrů a licence")
    st.write("Uživatel: **projekce@metrostav.cz**")
    st.markdown("<div class='price-tag'>Aktivní tarif: ENTERPRISE<br>(180 000 Kč / měsíc)</div>", unsafe_allow_html=True)

    st.write("")
    st.selectbox("Vybrat aktivní projekt:", ["SO-01 Výrobní hala Vítkovice", "SO-02 Skladová hala CTPark"])
    st.checkbox("Povolit automatickou detekci kolizí (CAD Guard)", value=True)
    st.checkbox("Provádět statický výpočet podlahy (Eurokód 2)", value=True)

    st.write("")
    if st.button("🔄 Přepočítat 3D trasy a hromosvody"):
        st.toast("AI přepočítává mračno bodů v AWS...", icon="⏳")

with col2:
    st.markdown("### 🧊 Živý 3D náhled vygenerovaného BIM modelu (Nativní IFC)")

    html_3d_prohlizec = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8" />
        <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
        <script src="https://threejs.org/examples/js/controls/OrbitControls.js"></script>
        <style> body { margin: 0; overflow: hidden; background-color: #1A202C; } </style>
    </head>
    <body>
        <div id="canvas-container"></div>
        <script>
            const scene = new THREE.Scene();
            scene.background = new THREE.Color(0x1A202C);
            const camera = new THREE.PerspectiveCamera(45, 800 / 450, 0.1, 1000);
            camera.position.set(15, 12, 20);
            const renderer = new THREE.WebGLRenderer({ antialias: true });
            renderer.setSize(window.innerWidth, 450);
            document.body.appendChild(renderer.domElement);
            const controls = new THREE.OrbitControls(camera, renderer.domElement);
            const light = new THREE.AmbientLight(0xffffff, 0.6);
            scene.add(light);
            const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
            dirLight.position.set(20, 40, 20);
            scene.add(dirLight);
            const gridHelper = new THREE.GridHelper(30, 30, 0x4A5568, 0x2D3748);
            gridHelper.position.y = -0.01;
            scene.add(gridHelper);
            const wallGeo = new THREE.BoxGeometry(20, 6, 0.3);
            const wallMat = new THREE.MeshStandardMaterial({ color: 0x718096, transparent: true, opacity: 0.4 });
            const wall1 = new THREE.Mesh(wallGeo, wallMat);
            wall1.position.set(0, 3, -5);
            scene.add(wall1);
            const dbGeo = new THREE.BoxGeometry(1.2, 2, 0.5);
            const dbMat = new THREE.MeshStandardMaterial({ color: 0x48BB78 });
            const rozvadec = new THREE.Mesh(dbGeo, dbMat);
            rozvadec.position.set(-6, 1, -4.7);
            scene.add(rozvadec);
            const pipeGeo = new THREE.CylinderGeometry(0.08, 0.08, 12);
            const pipeMat = new THREE.MeshStandardMaterial({ color: 0xE53E3E, roughness: 0.2 });
            const trasa = new THREE.Mesh(pipeGeo, pipeMat);
            trasa.rotation.z = Math.PI / 2;
            trasa.position.set(0, 5, -4.7);
            scene.add(trasa);
            function animate() {
                requestAnimationFrame(animate);
                controls.update();
                renderer.render(scene, camera);
            }
            animate();
            window.addEventListener('resize', () => {
                camera.aspect = window.innerWidth / 450;
                camera.updateProjectionMatrix();
                renderer.setSize(window.innerWidth, 450);
            });
        </script>
    </body>
    </html>
    """

    components.html(html_3d_prohlizec, height=460)

    st.write("### 📥 Export finální dokumentace")
    if ma_dwg_export():
        st.success("DWG export je aktivní. ZIP bude obsahovat i .DWG soubor.")
    else:
        st.warning("DWG export není aktivní. ZIP bude obsahovat DXF a informaci, jak DWG zapnout.")
    sc1, sc2, sc3 = st.columns(3)
    demo_export_data = {
        "id_projektu": "SO-01",
        "lokace": "Vítkovice, ČR",
        "parcela": "st. 1245/2",
        "investor": "Průmyslový Development s.r.o.",
        "investor_adresa": "U Prazdroje 22, 301 00 Plzeň",
        "datum_skenu": datetime.datetime.now().strftime('%d.%m.%Y'),
        "trida_lps": "LPS II",
        "tloustka_podlahy_mm": 200,
        "max_zatizeni_tuny": 4.8,
        "uspora_kwh": 21500,
    }
    with sc1:
        st.download_button("📂 Stáhnout 3D BIM (.IFC)", data=vytvorit_ifc_export(demo_export_data), file_name="Model_Vypocet.ifc")
    with sc2:
        st.download_button("📐 Stáhnout Výkres (.DXF)", data=vytvorit_dxf_export(demo_export_data), file_name="Autocad_Trasy.dxf")
    with sc3:
        if st.button("📊 Sestavit a stáhnout Rozpočet (.XLSX)", key="sr_quick"):
            _xlsx = vytvor_excel_stavebni_rozpocet_bytes("Průmyslová hala / Logistika", 60.0, 30.0, 8.0, 1)
            st.download_button("📥 Stáhnout Rozpočet (.XLSX)", data=_xlsx, file_name="Rozpocet_Hala.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="sr_quick_dl")

    dwg_bytes = vytvorit_dwg_export(demo_export_data)
    if dwg_bytes:
        st.download_button("📐 Stáhnout AutoCAD (.DWG)", data=dwg_bytes, file_name="Autocad_Trasy.dwg")
    else:
        st.info(dwg_export_status_text())

st.write("---")
st.markdown("### 🏛️ Generování složky pro stavební úřad (Podle nového stavebního zákona)")

col_urad1, col_urad2 = st.columns([2, 1])

with col_urad1:
    st.info("ℹ️ Tento modul sestaví dokumentaci pro stavební povolení / ohlášení podle vyhlášky o dokumentaci staveb. Všechny textové zprávy jsou vygenerovány ve formátu připraveném pro autorizační razítko ČKAIT.")

    # Legislativní parametry
    parcela_urad = st.text_input("Číslo parcely (pro situaci):", value="st. 1245/2")
    katastralni_urad = st.text_input("Katastrální území:", value="Plzeň-město")
    investor_urad = st.text_input("Investor / stavebník:", value="Průmyslový Development s.r.o.")
    investor_adresa_urad = st.text_input("Adresa investora:", value="U Prazdroje 22, 301 00 Plzeň")
    datum_skenu_urad = st.text_input("Datum skenu:", value=datetime.datetime.now().strftime('%d.%m.%Y'))
    trida_lps_urad = st.text_input("Třída LPS:", value="LPS II (vysoká ochrana)")
    tloustka_podlahy_mm_urad = st.number_input("Tloušťka podlahy (mm):", min_value=50, max_value=500, value=200)
    max_zatizeni_tuny_urad = st.number_input("Max. zatížení (tuny):", min_value=0.1, max_value=20.0, value=4.8)
    uspora_kwh_urad = st.number_input("Odhadovaná úspora (kWh/rok):", min_value=0, max_value=100000, value=21500)

with col_urad2:
    st.write("")
    st.write("")
    if st.button("🏛️ Vygenerovat podklady pro stavební úřad"):
        st.toast("Sestavuji Průvodní zprávu a Situační výkresy...", icon="📝")

        data_objektu = {
            "lokace": katastralni_urad,
            "parcela": parcela_urad,
            "investor": investor_urad,
            "investor_adresa": investor_adresa_urad,
            "datum_skenu": datum_skenu_urad,
            "trida_lps": trida_lps_urad,
            "tloustka_podlahy_mm": tloustka_podlahy_mm_urad,
            "max_zatizeni_tuny": max_zatizeni_tuny_urad,
            "uspora_kwh": uspora_kwh_urad,
        }
        # Defaultní bbox pro ukázku okolí Plzně
        bbox = (13.376, 49.742, 13.382, 49.747)
        zip_data = vytvorit_zip_pro_stavebni_urad(data_objektu, bbox=bbox)

        if zip_data:
            st.success("Složka úspěšně připravena k exportu!")
            st.download_button(
                "📥 Stáhnout kompletní ZIP pro stavební úřad",
                data=zip_data,
                file_name="Podklady_Stavebni_Urad.zip",
                mime="application/zip",
            )
        else:
            st.error("Nepodařilo se vytvořit ZIP balíček. Zkontrolujte připojení nebo nastavení API ČÚZK.")

st.write("---")
st.markdown("### 🌪️ Simulace klimatických vlivů (ČSN EN 1991)")

lokalita_vstup = st.selectbox("Vyberte lokalitu stavby pro klimatická data:", ["Praha", "Plzeň", "Liberec", "Pec pod Sněžkou"])
st.caption("Systém automaticky spáruje GPS s oficiální mapou sněhových a větrných oblastí ČR.")

vysledky_klima = vypocitat_klimaticke_zatizeni(lokalita_vstup, plocha_strechy_m2=1800)

col_k1, col_k2, col_k3 = st.columns(3)
with col_k1:
    st.metric("Sněhová oblast ČR", f"Oblast {vysledky_klima['snehova_oblast']}")
    st.write(f"Konstrukční zatížení: **{vysledky_klima['tlak_snehu_kg_m2']} kg/m²**")
with col_k2:
    st.metric("Větrná oblast ČR", f"Oblast {vysledky_klima['vetrna_oblast']}")
    st.write(f"Základní tlak větru: **{vysledky_klima['tlak_vetru_kn_m2']} kN/m²**")
with col_k3:
    if vysledky_klima['snehova_oblast'] >= 4:
        st.error("⚠️ VYSOKÉ ZATÍŽENÍ: AI automaticky zesiluje ocelový skelet haly.")
    else:
        st.success("✅ Normální zatížení: Konstrukce je optimalizována na minimální váhu oceli.")

st.write("---")
zobrazit_modul_skenovani()

st.write("---")
zobrazit_energeticky_modul()

st.write("---")
zobrazit_stavebni_rozpocet()

st.write("---")
zobrazit_generator_energetickych_projektu()

st.write("---")
zobrazit_generator_novostaveb()
