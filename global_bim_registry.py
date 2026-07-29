import sqlite3
import datetime
import secrets

class GlobalBIMRegistry:
    def __init__(self, db_name="enterprise_expert_system.db"):
        self.db_name = db_name
        self.inicializovat_strukturu()

    def inicializovat_strukturu(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # 1. Tabulka uživatelů a jejich rolí (MAJITEL vs ZAMĚSTNANEC)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uzivatele (
                email TEXT PRIMARY KEY,
                id_firmy TEXT NOT NULL,
                jmeno TEXT NOT NULL,
                role TEXT NOT NULL -- 'MAJITEL', 'PROJEKTANT'
            )
        """)
        
        # 2. Tabulka projektů
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projekty (
                id_projektu TEXT PRIMARY KEY,
                id_firmy TEXT NOT NULL,
                nazev_projektu TEXT NOT NULL,
                vytvoril_uzivatel TEXT NOT NULL,
                token_pro_sdileni TEXT UNIQUE,
                FOREIGN KEY (vytvoril_uzivatel) REFERENCES uzivatele(email)
            )
        """)
        
        # 3. Tabulka verzí projektů (Sledování historie změn)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verze_projektu (
                id_verze INTEGER PRIMARY KEY AUTOINCREMENT,
                id_projektu TEXT NOT NULL,
                cislo_verze INTEGER NOT NULL,
                popis_zmeny TEXT,
                datum_zmeny TEXT,
                cad_url TEXT,
                bim_url TEXT,
                status_security TEXT,
                FOREIGN KEY (id_projektu) REFERENCES projekty(id_projektu)
            )
        """)
        conn.commit()
        conn.close()

    def vytvorit_token_pro_sdileni(self, id_projektu):
        """Vygeneruje bezpečný unikátní odkaz pro externí sdílení projektu."""
        token = secrets.token_urlsafe(16)
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("UPDATE projekty SET token_pro_sdileni = ? WHERE id_projektu = ?", (token, id_projektu))
        conn.commit()
        conn.close()
        return f"https://expert-system.com/{token}"

    def zaregistrovat_uzivatele(self, email, id_firmy, jmeno, role):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO uzivatele (email, id_firmy, jmeno, role) VALUES (?, ?, ?, ?)",
            (email, id_firmy, jmeno, role)
        )
        conn.commit()
        conn.close()

    def vytvorit_projekt(self, id_projektu, id_firmy, nazev_projektu, vytvoril_uzivatel):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO projekty (id_projektu, id_firmy, nazev_projektu, vytvoril_uzivatel) VALUES (?, ?, ?, ?)",
            (id_projektu, id_firmy, nazev_projektu, vytvoril_uzivatel)
        )
        conn.commit()
        conn.close()

    def pridat_verzi_projektu(self, id_projektu, cislo_verze, popis_zmeny, cad_url, bim_url, status_security):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        datum_zmeny = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO verze_projektu (id_projektu, cislo_verze, popis_zmeny, datum_zmeny, cad_url, bim_url, status_security) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_projektu, cislo_verze, popis_zmeny, datum_zmeny, cad_url, bim_url, status_security)
        )
        conn.commit()
        conn.close()

    def ziskat_projekt_z_tokenu(self, token_pro_sdileni):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT id_projektu, id_firmy, nazev_projektu, vytvoril_uzivatel FROM projekty WHERE token_pro_sdileni = ?", (token_pro_sdileni,))
        projekt = cursor.fetchone()
        conn.close()
        return projekt

    def nacist_projekty_podle_prav(self, email_uzivatele):
        """
        Klíčová logika: Majitel vidí VŠECHNY projekty firmy. 
        Projektant vidí pouze své vlastní projekty.
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Zjištění role a firmy uživatele
        cursor.execute("SELECT id_firmy, role FROM uzivatele WHERE email = ?", (email_uzivatele,))
        user_data = cursor.fetchone()
        if not user_data:
            conn.close()
            return []
        
        id_firmy, role = user_data
        
        if role == "MAJITEL":
            # Majitel stahuje kompletní historii celé společnosti
            cursor.execute("""
                SELECT p.id_projektu, p.nazev_projektu, p.vytvoril_uzivatel, MAX(v.cislo_verze), v.status_security 
                FROM projekty p
                JOIN verze_projektu v ON p.id_projektu = v.id_projektu
                WHERE p.id_firmy = ?
                GROUP BY p.id_projektu
            """, (id_firmy,))
        else:
            # Projektant vidí jen své
            cursor.execute("""
                SELECT p.id_projektu, p.nazev_projektu, p.vytvoril_uzivatel, MAX(v.cislo_verze), v.status_security 
                FROM projekty p
                JOIN verze_projektu v ON p.id_projektu = v.id_projektu
                WHERE p.vytvoril_uzivatel = ?
                GROUP BY p.id_projektu
            """, (email_uzivatele,))
            
        projekty = cursor.fetchall()
        conn.close()
        return projekty
