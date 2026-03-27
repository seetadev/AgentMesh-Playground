"""W3C Contact Picker ContactAddress type.

Specification:
https://www.w3.org/TR/contact-picker/#contactaddress
"""

from typing import Optional

from pydantic import BaseModel, Field

CONTACT_ADDRESS_DATA_KEY = "contact_picker.ContactAddress"


class ContactAddress(BaseModel):
    """A ContactAddress represents a physical address.

    Specification:
    https://www.w3.org/TR/contact-picker/#contactaddress
    """

    city: Optional[str] = None
    country: Optional[str] = None
    dependent_locality: Optional[str] = None
    organization: Optional[str] = None
    phone_number: Optional[str] = None
    postal_code: Optional[str] = None
    recipient: Optional[str] = None
    region: Optional[str] = None
    sorting_code: Optional[str] = None
    address_line: Optional[list[str]] = None
