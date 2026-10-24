"""Does this business already have a website?

A Google listing with no website field does not mean there is no site: plenty of
businesses have one the listing never mentions. Opening with "I saw you have no
site" to someone who does is the worst first line this system can send, so the
likely addresses are tried before anyone is contacted.
"""

import asyncio
import re
import unicodedata

from harvey.collectors.discover import _GENERIC_NAME_WORDS, name_matches_domain
from harvey.public_http import public_http_client

TLDS = (".com.br", ".com")
FILLER = {"de", "da", "do", "das", "dos", "e", "a", "o", "ltda", "me", "epp", "eireli"}
TIMEOUT = 5
# Portuguese words for a kind of business: "Restaurante Santana" may live at santana.com.br.
GENERIC_PT = {"restaurante", "bar", "padaria", "pizzaria", "lanchonete", "salao", "barbearia", "clinica",
              "loja", "academia", "escritorio", "construtora", "imobiliaria", "autoescola", "escola",
              "floricultura", "marcenaria", "oficina", "mecanica", "petshop", "pet", "shop", "estetica",
              "veterinaria", "dentista", "advocacia", "contabilidade", "boteco", "cafe", "gastronomia"}


def candidate_domains(name: str) -> list[str]:
    """Likely addresses for a business name: the whole name, then the name
    without its generic words ("restaurante", "bar"...), in the usual TLDs."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in FILLER]
    distinctive = [w for w in words if w not in _GENERIC_NAME_WORDS | GENERIC_PT]
    stems = []
    for ws in (words, distinctive):
        stem = "".join(ws)
        if len(stem) >= (4 if ws is words else 6) and stem not in stems:
            stems.append(stem)
    return [stem + tld for stem in stems for tld in TLDS]


async def _answers(client, domain: str) -> bool:
    try:
        r = await client.get(f"https://{domain}", timeout=TIMEOUT)
    except Exception:
        return False
    return r.status_code < 400


async def find_own_site(name: str, client=None) -> str:
    """The domain of a site that belongs to this business, or "" (also when
    unsure: a lead is only skipped on evidence)."""
    domains = [d for d in candidate_domains(name) if name_matches_domain(name, d)]
    if not domains:
        return ""
    own = client is None
    client = client or public_http_client(timeout=TIMEOUT)
    try:
        found = await asyncio.gather(*(_answers(client, d) for d in domains))
    finally:
        if own:
            await client.aclose()
    return next((d for d, ok in zip(domains, found) if ok), "")
