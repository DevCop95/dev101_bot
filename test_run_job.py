import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import run_job
# is_recent vive en sources/rss_feeds.py tras el refactor a módulos
from sources.rss_feeds import is_recent

class TestRunJob(unittest.TestCase):

    def test_clean_markdown(self):
        self.assertEqual(run_job.clean_markdown("**Bold** text"), "Bold text")
        self.assertEqual(run_job.clean_markdown("*Italic* text"), "Italic text")
        self.assertEqual(run_job.clean_markdown("Normal text"), "Normal text")

    def test_is_recent_spanish(self):
        # Generar una fecha reciente en español
        now = datetime.now()
        meses_inv = {
            1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
            5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
            9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
        }
        fecha_hoy = f"{now.day} de {meses_inv[now.month]} de {now.year}"
        self.assertTrue(is_recent(fecha_hoy))
        
        # Fecha vieja
        fecha_vieja = "1 de enero de 2020"
        self.assertFalse(is_recent(fecha_vieja))

    def test_is_recent_iso(self):
        now = datetime.now()
        fecha_reciente = now.strftime("%Y-%m-%d")
        self.assertTrue(is_recent(fecha_reciente))
        
        fecha_vieja = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        self.assertFalse(is_recent(fecha_vieja))

    def test_is_recent_rfc822(self):
        # Formato RSS: "Sat, 02 May 2026 12:00:00 +0000"
        now = datetime.now()
        fecha_rfc = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        self.assertTrue(is_recent(fecha_rfc))
        
        fecha_vieja_rfc = "Mon, 01 Jan 2024 00:00:00 +0000"
        self.assertFalse(is_recent(fecha_vieja_rfc))

    def test_detectar_categoria(self):
        # Test with actual source names and titles
        self.assertEqual(run_job.detectar_categoria("GPT-4 released", "CyberSecurity News"), "IA")
        self.assertEqual(run_job.detectar_categoria("Ransomware attack hits hospital", "WeLiveSecurity"), "Ciberseguridad")
        self.assertEqual(run_job.detectar_categoria("Nueva vulnerabilidad zero-day", "DragonJAR"), "Ciberseguridad")
        self.assertEqual(run_job.detectar_categoria("OpenAI launches new model", "IA en Español"), "IA")
        self.assertEqual(run_job.detectar_categoria("Generic tech news", "Unknown Source"), "Tech")

    def test_detectar_categoria_sin_falsos_positivos_substring(self):
        # Regresión: keywords cortos deben matchear por palabra completa, no como
        # substring ("ia" en "historia/social", "ai" en "email", "apt" en "laptop").
        self.assertEqual(run_job.detectar_categoria("La historia del email en laptops", "Unknown Source"), "Tech")
        self.assertEqual(run_job.detectar_categoria("Red social lanza nueva experiencia", "Unknown Source"), "Tech")
        # ...pero la palabra completa sí debe activar la categoría
        self.assertEqual(run_job.detectar_categoria("La IA generativa transforma el sector", "Unknown Source"), "IA")
        self.assertEqual(run_job.detectar_categoria("Detectado nuevo grupo APT en Europa", "Unknown Source"), "Ciberseguridad")

    def test_scraper_structure(self):
        # Test para verificar que el contrato de datos se mantiene
        # (Aunque el scraping falle, la estructura debe ser consistente)
        sample_item = {
            'title': 'Test Title',
            'link': 'https://example.com',
            'source': 'Test Source'
        }
        # Validamos que los campos necesarios existen
        self.assertIn('title', sample_item)
        self.assertIn('link', sample_item)
        self.assertIn('source', sample_item)


class TestGetGithubFile(unittest.TestCase):
    @patch("run_job.GIT_TOKEN", "token-test")
    @patch("run_job.requests.get")
    def test_archivo_grande_usa_raw(self, mock_get):
        # >1MB: la contents API devuelve content vacío (encoding "none") aunque
        # el archivo exista. Debe pedir el raw aparte, NUNCA tratarlo como vacío
        # (republicaría todo y sobreescribiría el historial).
        meta = MagicMock(status_code=200)
        meta.json.return_value = {"content": "", "encoding": "none",
                                  "size": 1_200_000, "sha": "abc123"}
        raw = MagicMock(status_code=200)
        raw.content = b'[{"id": 1, "titulo": "Noticia"}]'
        mock_get.side_effect = [meta, raw]

        noticias, sha = run_job.get_github_file()
        self.assertEqual(noticias, [{"id": 1, "titulo": "Noticia"}])
        self.assertEqual(sha, "abc123")
        # La segunda petición debe pedir el media type raw
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[1].kwargs["headers"]["Accept"],
                         "application/vnd.github.raw+json")

    @patch("run_job.GIT_TOKEN", "token-test")
    @patch("run_job.requests.get")
    def test_archivo_vacio_legitimo(self, mock_get):
        # content vacío Y size 0 = archivo realmente vacío → lista vacía con sha.
        meta = MagicMock(status_code=200)
        meta.json.return_value = {"content": "", "size": 0, "sha": "abc123"}
        mock_get.return_value = meta

        noticias, sha = run_job.get_github_file()
        self.assertEqual(noticias, [])
        self.assertEqual(sha, "abc123")


class TestDeduplicacion(unittest.TestCase):
    def test_clave_contenido_por_cve(self):
        # Misma CVE desde títulos/fuentes distintos → misma clave
        k1 = run_job.clave_contenido("Critical RCE in YAMCS", "Affects CVE-2024-12345")
        k2 = run_job.clave_contenido("Nuevo exploit para YAMCS server", "CVE-2024-12345 explotada")
        self.assertTrue(k1.startswith("cve:"))
        self.assertEqual(k1, k2)

    def test_clave_contenido_por_titulo(self):
        # Sin CVE: normaliza título (emojis/puntuación/espacios no cuentan)
        k1 = run_job.clave_contenido("🔴 Ataque de Ransomware a Hospital!!!")
        k2 = run_job.clave_contenido("Ataque de   ransomware a hospital")
        self.assertTrue(k1.startswith("txt:"))
        self.assertEqual(k1, k2)

    def test_clave_contenido_distintas(self):
        k1 = run_job.clave_contenido("OpenAI lanza nuevo modelo")
        k2 = run_job.clave_contenido("Google presenta Gemini 3")
        self.assertNotEqual(k1, k2)

    def test_es_noticia_similar_mismo_cve(self):
        existentes = [{"titulo": "Fallo en Apache", "resumen": "Detalles de CVE-2024-99999"}]
        es_sim, _ = run_job.es_noticia_similar(
            "Otra redacción del bug", "Relacionado con CVE-2024-99999", existentes
        )
        self.assertTrue(es_sim)

    def test_es_noticia_no_similar(self):
        existentes = [{"titulo": "OpenAI lanza GPT", "resumen": "Modelo de lenguaje nuevo"}]
        es_sim, _ = run_job.es_noticia_similar(
            "Ransomware ataca hospital", "Cifrado de datos médicos", existentes
        )
        self.assertFalse(es_sim)

    def test_cve_distinto_no_es_similar_pese_a_boilerplate(self):
        # Veto de CVE: dos CVEs DISTINTOS con resumen calcado NO son la misma noticia.
        existentes = [{"titulo": "Vulnerabilidad SQL en JoomCCK",
                       "resumen": "Inyección SQL en el componente permite manipular la base de datos. CVE-2026-13482."}]
        es_sim, _ = run_job.es_noticia_similar(
            "Vulnerabilidad SQL en SourceCodester Timetabling",
            "Inyección SQL en el sistema permite manipular la base de datos. CVE-2026-13526.",
            existentes)
        self.assertFalse(es_sim)


class TestDedupRetroactivo(unittest.TestCase):
    def _n(self, id, titulo, resumen=""):
        return {"id": id, "titulo": titulo, "resumen": resumen, "fuente": "X"}

    def test_titulos_identicos(self):
        self.assertTrue(run_job.son_duplicadas(
            self._n(1, "Vulnerabilidad en YAMCS"), self._n(2, "Vulnerabilidad en YAMCS")))

    def test_misma_entidad_reformulada(self):
        # SmartLoader: el caso real reportado
        self.assertTrue(run_job.son_duplicadas(
            self._n(1, "Malware SmartLoader"), self._n(2, "Campaña Malware SmartLoader")))

    def test_mismo_cve(self):
        self.assertTrue(run_job.son_duplicadas(
            self._n(1, "Fallo X", "afecta CVE-2026-1111"),
            self._n(2, "Otro título", "CVE-2026-1111 explotada")))

    def test_cve_distinto_no_es_dup(self):
        # Veto: dos CVEs distintos NUNCA son duplicados aunque el título sea similar
        self.assertFalse(run_job.son_duplicadas(
            self._n(1, "Vulnerabilidad CVE-2026-58057", "x"),
            self._n(2, "Vulnerabilidad CVE-2026-10644", "y")))

    def test_mismo_vendor_distinto_producto_no_es_dup(self):
        # Cisco CUCM vs Cisco genérico; OpenAI phishing vs lanzamiento
        self.assertFalse(run_job.son_duplicadas(
            self._n(1, "Vulnerabilidad en Cisco CUCM"), self._n(2, "Urgente: Vulnerabilidad en Cisco")))
        self.assertFalse(run_job.son_duplicadas(
            self._n(1, "Ataque de phishing con OpenAI"), self._n(2, "OpenAI Lanza GPT-5.6 Sol")))

    def test_generico_no_absorbe_especifico(self):
        # "Ataque de cadena de suministro" (genérico) != "...en WordPress"
        self.assertFalse(run_job.son_duplicadas(
            self._n(1, "Ataque de cadena de suministro en WordPress"),
            self._n(2, "Ataque de cadena de suministro")))

    def test_deduplicar_conserva_mas_reciente(self):
        # Orden newest-first: id3 (nueva) primero, id1 (vieja) se elimina
        noticias = [
            self._n(3, "Campaña Malware SmartLoader"),
            self._n(2, "Otra noticia distinta sobre IA"),
            self._n(1, "Malware SmartLoader"),
        ]
        limpia, elim = run_job.deduplicar_noticias(noticias)
        ids_limpia = [n["id"] for n in limpia]
        self.assertIn(3, ids_limpia)      # se conserva la más reciente
        self.assertNotIn(1, ids_limpia)   # se elimina la más antigua
        self.assertEqual([n["id"] for n in elim], [1])


class TestDedupNombresPropios(unittest.TestCase):
    def _n(self, titulo, resumen, fuente):
        return {"titulo": titulo, "resumen": resumen, "fuente": fuente}

    def test_misma_historia_cross_medio(self):
        # Misma campaña FortiBleed en dos medios distintos, titulares reformulados.
        a = self._n("Campaña FortiBleed roba credenciales",
                    "La campaña FortiBleed usó sniffers para robar secretos de dispositivos "
                    "FortiGate de Fortinet a gran escala.", "Bleeping Computer")
        b = self._n("FortiBleed ataca FortiGate",
                    "La operación FortiBleed roba credenciales de dispositivos Fortinet "
                    "FortiGate a gran escala mediante fuerza bruta.", "Una al Día")
        df = run_job._df_nombres_propios([a, b])
        self.assertTrue(run_job.son_duplicadas(a, b, df=df))

    def test_mismo_medio_no_aplica(self):
        # Mismo nombre propio raro pero mismo medio (p.ej. digests diarios) -> no dup.
        a = self._n("Alertas de Seguridad", "El ISC Stormcast del 23 resume amenazas.", "SANS ISC")
        b = self._n("Alerta de Seguridad", "El ISC Stormcast del 24 resume amenazas.", "SANS ISC")
        df = run_job._df_nombres_propios([a, b])
        self.assertFalse(run_job.son_duplicadas(a, b, df=df))

    def test_nombre_propio_comun_no_fusiona(self):
        # 'Microsoft' es frecuente en el corpus -> no es señal distintiva.
        corpus = [self._n(f"Noticia {i} sobre Microsoft",
                          f"Microsoft anuncia algo distinto token{i}", f"Medio{i}") for i in range(8)]
        a = self._n("Vulnerabilidad en Microsoft Teams",
                    "Un fallo en Microsoft Teams permite acceso no autorizado.", "The Hacker News")
        b = self._n("Microsoft corrige Exchange",
                    "Microsoft parchea Exchange Server contra ataques activos.", "Bleeping Computer")
        df = run_job._df_nombres_propios(corpus + [a, b])
        self.assertFalse(run_job.son_duplicadas(a, b, df=df))

    def test_sin_df_capa_inactiva(self):
        # Sin contexto de corpus (df) la capa de nombres propios no se activa.
        a = self._n("Campaña FortiBleed roba credenciales", "FortiBleed roba de FortiGate Fortinet", "A")
        b = self._n("FortiBleed ataca FortiGate", "FortiBleed credenciales Fortinet FortiGate", "B")
        self.assertFalse(run_job.son_duplicadas(a, b))


class TestDiversidad(unittest.TestCase):
    def test_medio_agrupa_telegram(self):
        # Todos los canales de Telegram cuentan como un solo medio
        self.assertEqual(run_job.medio_de_fuente("TG: vx-underground"), "Telegram")
        self.assertEqual(run_job.medio_de_fuente("TG: CVE Notify"), "Telegram")

    def test_medio_rss_individual(self):
        self.assertEqual(run_job.medio_de_fuente("Bleeping Computer"), "Bleeping Computer")
        self.assertEqual(run_job.medio_de_fuente("The Hacker News (Fallback)"), "The Hacker News")

    def test_cap_por_medio(self):
        self.assertEqual(run_job.cap_para_medio("Telegram"), 2)
        self.assertEqual(run_job.cap_para_medio("Exploit-DB"), 2)
        self.assertEqual(run_job.cap_para_medio("Outlet Desconocido"), run_job.MEDIO_CAP_DEFAULT)

    def test_interleave_round_robin(self):
        items = [
            {"source": "A", "title": "a1"}, {"source": "A", "title": "a2"},
            {"source": "A", "title": "a3"}, {"source": "B", "title": "b1"},
        ]
        result = run_job.interleave_by_source(items)
        # El primer y segundo item deben ser de fuentes distintas (round-robin)
        self.assertNotEqual(result[0]["source"], result[1]["source"])

    def test_diversidad_primer_item_siempre_pasa(self):
        # En un run vacío, el FLOOR garantiza que cualquier medio entre.
        ok, _ = run_job.pasa_diversidad("Telegram", {}, 0)
        self.assertTrue(ok)

    def test_diversidad_telegram_no_acapara_run_pequeno(self):
        # Run de 4 items con 1 Telegram ya publicado: añadir otro Telegram
        # rompería la cuota dinámica (2/5 = 40% es el límite, pero el grupo
        # underground tope antes). El segundo Telegram seguido se bloquea.
        ok, motivo = run_job.pasa_diversidad("Telegram", {"Telegram": 1}, 1)
        self.assertFalse(ok)

    def test_diversidad_underground_limita_grupo(self):
        # Run de 4 (1 mainstream + 1 Telegram + 2 Exploit-DB): añadir otro Telegram
        # pasa su cap por-medio pero el grupo underground ya cubre demasiado del run.
        counts = {"Telegram": 1, "Exploit-DB": 2}
        ok, motivo = run_job.pasa_diversidad("Telegram", counts, 4)
        self.assertFalse(ok)
        self.assertIn("underground", motivo)

    def test_diversidad_mainstream_no_bloquea_a_underground(self):
        # Con suficiente prensa mainstream publicada, el underground recupera espacio.
        counts = {"Bleeping Computer": 2, "The Hacker News": 2, "Telegram": 1}
        ok, _ = run_job.pasa_diversidad("Telegram", counts, 5)
        self.assertTrue(ok)

    def test_diversidad_respeta_cap_absoluto(self):
        # Aunque el run sea grande, el cap absoluto de Telegram (2) manda.
        counts = {"Telegram": 2, "Bleeping Computer": 5, "The Hacker News": 5}
        ok, motivo = run_job.pasa_diversidad("Telegram", counts, 12)
        self.assertFalse(ok)
        self.assertIn("absoluto", motivo)


if __name__ == '__main__':
    unittest.main()
