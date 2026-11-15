"""Product registry: the avionics whose firmware this package knows how to handle."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


class FirmwareError(Exception):
    """A firmware check, download, package or card operation failed."""


# Package kinds (how a downloaded package becomes a card image)
KIND_GCD_FOLDER = "gcd_folder"   # zip holding a Garmin/ folder of .GCD files
KIND_COPY2CARD = "copy2card"     # zip holding a Garmin Copy2Card 7-Zip SFX (.exe)


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    page_url: str
    kind: str
    # Flight-log header "product" prefix that identifies this avionics on a card
    # (lets the installed version be read from the card). None = not available.
    log_product_prefix: Optional[str] = None
    # What the pilot must do after a card was written (shown in the notification).
    next_steps: Tuple[str, ...] = ()


PRODUCTS: Dict[str, Product] = {
    "g3x_touch": Product(
        key="g3x_touch",
        name="G3X Touch",
        page_url="https://www8.garmin.com/support/download_details.jsp?id=8901",
        kind=KIND_GCD_FOLDER,
        log_product_prefix="GDU 4",
        next_steps=(
            "Power up each display with this card inserted and confirm the update prompt.",
            "Do not remove power while the display shows 'Loading'; repeat for every display in the aircraft.",
        ),
    ),
    "gtn_xi": Product(
        key="gtn_xi",
        name="GTN Xi",
        page_url="https://www8.garmin.com/support/download_details.jsp?id=15519",
        kind=KIND_COPY2CARD,
        next_steps=(
            "This is a GTN loader card, not the database card. Keep it separate from the navigation database card.",
            "Load the software as described in Garmin's service bulletin. Powering a GTN with a loader card "
            "inserted and not loading the software can corrupt its configuration.",
        ),
    ),
}


def get_product(key: str) -> Product:
    try:
        return PRODUCTS[key]
    except KeyError:
        raise FirmwareError(f"Unknown firmware product '{key}' (known: {', '.join(PRODUCTS)})")


def page_url_for(product: Product, override: Optional[str] = None) -> str:
    return override or product.page_url
