VAT = {"DE": 0.19, "FR": 0.20, "NL": 0.21}


def vat_rate(country: str) -> float:
    return VAT.get(country, 0.0)
