# CRM-RTU-EMS
cRM RTU EMS Monitoring

## Spuštění

Projekt je připraven pro lokální běh přes Python 3.12+.

```bash
./.venv/bin/python -m pip install -r requirements.txt
```

API server:

```bash
./.venv/bin/uvicorn app_api:app --reload
```

Streamlit web:

```bash
./.venv/bin/streamlit run app_web_3d.py
```

Full verze projektu jedním příkazem:

```bash
./.venv/bin/python run_full_app.py
```

Docker spuštění:

```bash
docker build -t crm-rtu-ems .
docker run --rm -p 8000:8000 -p 8501:8501 -v "$PWD":/app -e ODAFILECONVERTER_PATH=/opt/oda/ODAFileConverter crm-rtu-ems
```

Docker Compose s ODAFileConverterem:

```bash
mkdir -p third_party/oda
# sem vložte binárku ODAFileConverter
docker compose up --build
```

Binárka se očekává v `third_party/oda/ODAFileConverter` a v kontejneru je namountovaná jako `/opt/oda/ODAFileConverter`.

DWG export v kontejneru je aktivní, pokud namountujete nebo nainstalujete `ODAFileConverter` a nastavíte `ODAFILECONVERTER_PATH` na jeho binárku. Bez toho zůstane DXF export plně funkční.

DWG export je dostupný, pokud je v systému nainstalovaný externí konvertor ODAFileConverter nebo je nastavena proměnná `ODAFILECONVERTER_PATH`. Bez něj se do ZIP balíčku uloží informativní soubor s poznámkou a k dispozici zůstane DXF.

## Kontrola

Ověřené moduly:

- `app_api.py`
- `app_web_3d.py`
- `centralni_evidence.py`
- `excel_energeticka_navratnost.py`
- `global_bim_registry.py`
- `modul_skenovani.py`

## Co projekt umí

- FastAPI endpoint pro generování projektu a zápis do evidence.
- Streamlit UI pro generativní návrhy, rozpočet, skenování, energetické výpočty a exporty.
- Export ZIP balíčku s průvodní zprávou, IFC, DXF, rozpočtem a metadaty.
- Excel protokoly pro skenování, ROI, ESG a stavební rozpočty.

Poznámka: Streamlit web je potřeba spouštět přímo přes `streamlit run`, jinak se při prostém importu zobrazí jen varování o session state.
