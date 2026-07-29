import sqlite3
import datetime

class CentralniEvidenceProjektu:
    def __init__(self, db_jmeno="evidence_bim_projektu.db"):
        self.db_jmeno = db_jmeno
        self.inicializovat_databazi()

    def inicializovat_databazi(self):
        """Vytvoří bezpečné databázové tabulky pro evidenci firem a jejich projektů."""
        conn = sqlite3.connect(self.db_jmeno)
        cursor = conn.cursor()
        
        # Tabulka firem (klientů)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS firmy (
                id_firmy TEXT PRIMARY KEY,
                nazev_firmy TEXT NOT NULL,
                typ_licence TEXT NOT NULL
            )
        """)
        
        # Tabulka projektů s vazbou na firmu a bezpečnostní status
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projekty (
                id_projektu TEXT PRIMARY KEY,
                id_firmy TEXT NOT NULL,
                nazev_projektu TEXT NOT NULL,
                lokace TEXT,
                datum_vytvoreni TEXT,
                vystup_cad_url TEXT,
                vystup_bim_url TEXT,
                bezpecnostni_status TEXT,
                FOREIGN KEY (id_firmy) REFERENCES firmy (id_firmy)
            )
        """)
        conn.commit()
        conn.close()

    def zaregistrovat_firmu(self, id_firmy, nazev_firmy, typ_licence):
        conn = sqlite3.connect(self.db_jmeno)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO firmy VALUES (?, ?, ?)", (id_firmy, nazev_firmy, typ_licence))
        conn.commit()
        conn.close()

    def ulozit_novy_projekt(self, id_projektu, id_firmy, nazev, lokace, cad_url, bim_url, status_bezpecnosti):
        """Zanese kompletní data o vygenerovaném projektu do firemního archivu."""
        conn = sqlite3.connect(self.db_jmeno)
        cursor = conn.cursor()
        datum_ted = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
            INSERT OR REPLACE INTO projekty VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_projektu, id_firmy, nazev, lokace, datum_ted, cad_url, bim_url, status_bezpecnosti))
        
        conn.commit()
        conn.close()
        print(f"🗄️ Projekt '{nazev}' byl bezpečně zaevidován do archivu společnosti (ID: {id_firmy}).")

    def nacist_projekty_firmy(self, id_firmy):
        """Vytáhne z databáze kompletní historii projektů pouze pro danou společnost."""
        conn = sqlite3.connect(self.db_jmeno)
        cursor = conn.cursor()
        cursor.execute("SELECT id_projektu, nazev_projektu, lokace, datum_vytvoreni, bezpecnostni_status FROM projekty WHERE id_firmy = ?", (id_firmy,))
        projekty = cursor.fetchall()
        conn.close()
        return projekty
