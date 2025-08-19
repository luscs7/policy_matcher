# fetch_ibge_geo.py
# Gera data/geo/ufs.csv e data/geo/municipios.csv usando a API do IBGE

import os
import time
import csv
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEO_DIR = os.path.join(BASE_DIR, "data", "geo")
os.makedirs(GEO_DIR, exist_ok=True)

UF_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados?orderBy=nome"
MUN_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?orderBy=nome"

def fetch_json(url, retries=5, timeout=30):
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(1 + i)  # backoff simples
    raise RuntimeError(f"Falha ao buscar {url}: {last_err}")

def save_ufs(ufs, path):
    # Campos: uf (sigla), uf_nome (nome por extenso), ibge_uf (código numérico)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["uf", "uf_nome", "ibge_uf"])
        for uf in sorted(ufs, key=lambda x: x.get("sigla", "")):
            w.writerow([uf.get("sigla",""), uf.get("nome",""), str(uf.get("id",""))])

def save_municipios(muns, path):
    # Campos: ibge_mun (id), nome_mun (nome), uf (sigla)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ibge_mun", "nome_mun", "uf"])
        for m in muns:
            ibge = str(m.get("id",""))
            nome = m.get("nome","")
            # A sigla da UF vem aninhada em microrregiao -> mesorregiao -> UF -> sigla
            try:
                uf_sigla = m["microrregiao"]["mesorregiao"]["UF"]["sigla"]
            except Exception:
                uf_sigla = ""
            w.writerow([ibge, nome, uf_sigla])

def main():
    print("Baixando UFs do IBGE…")
    ufs = fetch_json(UF_URL)
    ufs_csv = os.path.join(GEO_DIR, "ufs.csv")
    save_ufs(ufs, ufs_csv)
    print(f"✔ UFs salvas em: {ufs_csv}")

    print("Baixando municípios do IBGE… (pode levar alguns segundos)")
    muns = fetch_json(MUN_URL)
    muns_csv = os.path.join(GEO_DIR, "municipios.csv")
    save_municipios(muns, muns_csv)
    print(f"✔ Municípios salvos em: {muns_csv}")

    # Resumo
    uf_count = len(ufs)
    mun_count = len(muns)
    print(f"\nResumo: {uf_count} UFs e {mun_count} municípios gerados.")

if __name__ == "__main__":
    main()
