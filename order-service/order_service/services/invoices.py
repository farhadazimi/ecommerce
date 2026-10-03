"""PDF invoice generation, stored through StorageService at ``invoices/{yyyy}/{mm}/{number}.pdf``."""

from __future__ import annotations

import io
import logging
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from ecommerce_common.config import Settings
from ecommerce_common.errors import ConflictError
from ecommerce_common.log import log_event
from ecommerce_common.metrics import INVOICES
from ecommerce_common.models import Order, OrderStatus, PaymentStatus, utcnow
from ecommerce_common.storage import StorageService, invoice_key

logger = logging.getLogger("ecommerce.invoices")

INVOICEABLE = (OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED)


def invoice_number_for(order: Order) -> str:
    when = order.paid_at or order.created_at
    return f"INV-{when:%Y%m}-{order.id:06d}"


def render_invoice_pdf(order: Order, invoice_number: str, company: str = "Plan B Shop") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {invoice_number}", author=company,
    )
    styles = getSampleStyleSheet()
    e = lambda v: escape(str(v)) if v is not None else ""  # noqa: E731 - user data is escaped for Paragraph markup
    paid = next((p for p in reversed(order.payments) if p.status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED)), None)
    payment_status = paid.status if paid else "UNPAID"
    story = [
        Paragraph(f"<b>{e(company)}</b>", styles["Title"]),
        Paragraph(f"INVOICE <b>{e(invoice_number)}</b>", styles["Heading2"]),
        Spacer(1, 4 * mm),
    ]
    meta = [
        ["Invoice number", invoice_number, "Order", order.order_number],
        ["Invoice date", f"{(order.paid_at or utcnow()):%Y-%m-%d}", "Order date", f"{order.created_at:%Y-%m-%d %H:%M} UTC"],
        ["Customer", order.user.full_name, "E-mail", order.user.email],
        ["Payment status", payment_status, "Payment ref.",
         f"{paid.payment_ref} ({paid.card_brand or 'CARD'} ****{paid.card_last4 or ''})" if paid else "-"],
    ]
    meta_table = Table(meta, colWidths=[30 * mm, 55 * mm, 25 * mm, 64 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.grey), ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [meta_table, Spacer(1, 5 * mm)]
    address = "<br/>".join(e(v) for v in (
        order.shipping_name, order.shipping_address_line1, order.shipping_address_line2,
        f"{order.shipping_postal_code} {order.shipping_city}", order.shipping_country,
    ) if v)
    story += [Paragraph("<b>Ship to</b>", styles["Normal"]), Paragraph(address, styles["Normal"]), Spacer(1, 6 * mm)]

    cur = order.currency
    rows = [["Product", "SKU", "Qty", "Unit price", "Total"]]
    rows += [
        [Paragraph(e(i.product_name), styles["Normal"]), i.sku, str(i.quantity), f"{i.unit_price:.2f} {cur}", f"{i.line_total:.2f} {cur}"]
        for i in order.items
    ]
    rows += [
        ["", "", "", "Subtotal", f"{order.subtotal:.2f} {cur}"],
        ["", "", "", "Shipping", f"{order.shipping_fee:.2f} {cur}"],
        ["", "", "", "TOTAL", f"{order.total:.2f} {cur}"],
    ]
    n = len(rows)
    items_table = Table(rows, colWidths=[74 * mm, 30 * mm, 14 * mm, 28 * mm, 28 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, n - 4), 0.25, colors.lightgrey),
        ("LINEABOVE", (3, n - 3), (-1, n - 3), 0.5, colors.grey),
        ("FONTNAME", (3, n - 1), (-1, n - 1), "Helvetica-Bold"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [items_table, Spacer(1, 10 * mm),
              Paragraph("Thank you for your order. This invoice was generated electronically.", styles["Italic"])]
    doc.build(story)
    return buf.getvalue()


class InvoiceService:
    def __init__(self, storage: StorageService, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    def ensure_invoice(self, db: Session, order: Order) -> Order:
        """Generate + store the invoice if it does not exist yet (idempotent: same number, same key)."""
        if order.invoice_key:
            return order
        if order.status not in INVOICEABLE:
            raise ConflictError("An invoice is only available for paid orders", code="INVOICE_NOT_AVAILABLE")
        number = order.invoice_number or invoice_number_for(order)
        when = order.paid_at or order.created_at
        key = invoice_key(when.year, when.month, number)
        try:
            pdf = render_invoice_pdf(order, number)
            self.storage.upload(key, pdf, "application/pdf")
        except Exception:
            INVOICES.labels("failure").inc()
            log_event("INVOICE_FAILED", order_id=order.id, user_id=order.user_id)
            raise
        order.invoice_number = number
        order.invoice_key = key
        order.invoice_generated_at = utcnow()
        db.commit()
        INVOICES.labels("success").inc()
        log_event("INVOICE_GENERATED", order_id=order.id, user_id=order.user_id, invoice_number=number, object_key=key,
                  size_bytes=len(pdf))
        return order

    def download_url(self, order: Order) -> str:
        return self.storage.generate_signed_url(
            order.invoice_key, expires_in=600, download_name=f"{order.invoice_number}.pdf"  # type: ignore[arg-type]
        )
