import unittest

from modul_skenovani import (
    navrhnout_kabelove_trasy,
    vytvor_excel_nabidka_kabelove_trasy_bytes,
    vytvorit_schema_rozvadce_z_fotky,
    vytvor_excel_schema_rozvadce_bytes,
)
from app_web_3d import vytvorit_html_projektovy_posudek


class KabeloveTrasyTest(unittest.TestCase):
    def test_navrh_generuje_minimum_jednu_trasu_a_cenu(self):
        interior_data = {
            "mistnosti": [
                {
                    "cislo": 1,
                    "delka_m": 8.0,
                    "sirka_m": 5.0,
                    "vyska_m": 3.0,
                    "detekované_prvky": [
                        "Hlavní rozvaděč (RH)",
                        "Okno",
                        "Dveře",
                        "Zásuvka 230V",
                        "Datová zásuvka",
                        "Kabelová trasa",
                    ],
                }
            ]
        }

        result = navrhnout_kabelove_trasy(interior_data)

        self.assertIn("trasy", result)
        self.assertGreaterEqual(len(result["trasy"]), 1)
        self.assertGreater(result["celkova_cena_czk"], 0)
        self.assertEqual(result["trasy"][0]["typ"], "strop")

    def test_excel_export_vytvari_soubor(self):
        data = {
            "trasy": [
                {
                    "nazev": "Trasa A",
                    "typ": "strop",
                    "delka_m": 6.2,
                    "cena_czk": 3100,
                }
            ],
            "celkova_cena_czk": 3100,
        }

        xlsx_bytes = vytvor_excel_nabidka_kabelove_trasy_bytes(data, nazev_objektu="Demo")

        self.assertIsInstance(xlsx_bytes, bytes)
        self.assertGreater(len(xlsx_bytes), 0)

    def test_schema_rozvadce_z_fotky_vytvari_prvky(self):
        schema = vytvorit_schema_rozvadce_z_fotky(
            b"dummy-image-bytes",
            "rozvadec.jpg",
            prvky=["svetla", "zasuvky", "ventilator"],
        )

        self.assertIn("prvky", schema)
        self.assertGreaterEqual(len(schema["prvky"]), 3)
        self.assertTrue(any(prvek["typ"] == "svetla" for prvek in schema["prvky"]))

    def test_excel_schema_rozvadce_export_vytvari_soubor(self):
        schema = {
            "nazev": "Rozvaděč A",
            "prvky": [{"nazev": "SVĚTLA", "typ": "svetla", "circuit": "C1"}],
        }

        xlsx_bytes = vytvor_excel_schema_rozvadce_bytes(schema)

        self.assertIsInstance(xlsx_bytes, bytes)
        self.assertGreater(len(xlsx_bytes), 0)

    def test_html_projektovy_posudek_vytvari_kapitoly(self):
        html = vytvorit_html_projektovy_posudek(
            scan_data={"delka_m": 40, "sirka_m": 20, "plocha_m2": 800},
            thermo_data={"stav": "OK", "procento_anomalii": 0.5},
            interior_data={"mistnosti": [{"cislo": 1, "plocha_m2": 50}]},
            schema_data={"prvky": [{"nazev": "SVĚTLA", "typ": "svetla", "circuit": "C1"}]},
            trasy_data={"trasy": [{"nazev": "Trasa 1", "cena_czk": 2500}]},
            data_objektu={"lokace": "Plzeň", "investor": "Demo"},
        )

        self.assertIn("Projektová dokumentace", html)
        self.assertIn("Termovize", html)
        self.assertIn("Energetický posudek", html)


if __name__ == "__main__":
    unittest.main()
