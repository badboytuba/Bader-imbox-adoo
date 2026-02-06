# Copyright 2024 Modernized by OCA Contributors
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailWhatsAppCatalog(models.Model):
    """
    WhatsApp Product Catalog integration.
    
    Syncs Odoo products with Meta Commerce catalog and allows
    sending product messages via WhatsApp.
    """
    _name = "mail.whatsapp.catalog"
    _description = "WhatsApp Product Catalog"

    gateway_id = fields.Many2one(
        "mail.gateway",
        string="Gateway",
        required=True,
        domain=[("gateway_type", "=", "whatsapp")],
        ondelete="cascade",
    )
    catalog_id = fields.Char(
        string="Meta Catalog ID",
        help="Commerce catalog ID from Meta Business Manager",
    )
    name = fields.Char(required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("connected", "Connected"),
            ("syncing", "Syncing"),
            ("error", "Error"),
        ],
        default="draft",
    )
    last_sync = fields.Datetime(string="Last Sync")
    product_count = fields.Integer(
        compute="_compute_product_count",
        string="Products",
    )
    sync_product_ids = fields.Many2many(
        "product.template",
        string="Products to Sync",
        domain=[("sale_ok", "=", True)],
    )
    error_message = fields.Text(string="Error")

    def _compute_product_count(self):
        for record in self:
            record.product_count = len(record.sync_product_ids)

    def action_connect_catalog(self):
        """Connect and verify catalog ID with Meta"""
        self.ensure_one()
        
        if not self.catalog_id:
            raise UserError(_("Please enter the Meta Catalog ID"))
        
        gateway = self.gateway_id
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{self.catalog_id}"
            
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                params={"fields": "name,product_count"},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            self.write({
                "name": result.get("name", self.name),
                "state": "connected",
                "error_message": False,
            })
            
            _logger.info("Connected to catalog: %s", self.catalog_id)
            
        except requests.exceptions.RequestException as e:
            error = str(e)
            if hasattr(e, 'response') and e.response:
                error = e.response.text
            self.write({
                "state": "error",
                "error_message": error,
            })
            raise UserError(_("Failed to connect: %s") % error) from e

    def action_sync_products(self):
        """Sync selected Odoo products to Meta catalog"""
        self.ensure_one()
        
        if self.state != "connected":
            raise UserError(_("Please connect the catalog first"))
        
        self.write({"state": "syncing"})
        
        gateway = self.gateway_id
        synced = 0
        errors = []
        
        for product in self.sync_product_ids:
            try:
                # Prepare product data
                product_data = {
                    "retailer_id": str(product.id),
                    "name": product.name[:200],
                    "description": (product.description_sale or product.name)[:5000],
                    "availability": "in stock" if product.qty_available > 0 else "out of stock",
                    "price": int(product.list_price * 100),  # Price in cents
                    "currency": product.currency_id.name or "BRL",
                    "url": f"/shop/product/{product.id}",
                }
                
                # Add image if available
                if product.image_1920:
                    # Would need to upload image to public URL first
                    pass
                
                url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{self.catalog_id}/products"
                
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {gateway.token}"},
                    json=product_data,
                    timeout=30,
                )
                response.raise_for_status()
                synced += 1
                
            except Exception as e:
                errors.append(f"{product.name}: {str(e)}")
        
        self.write({
            "state": "connected",
            "last_sync": fields.Datetime.now(),
            "error_message": "\n".join(errors) if errors else False,
        })
        
        _logger.info("Synced %d products, %d errors", synced, len(errors))
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sync Complete"),
                "message": _("Synced %d products, %d errors") % (synced, len(errors)),
                "type": "success" if not errors else "warning",
            }
        }

    def send_product_message(self, recipient_phone, product_ids, body=None):
        """
        Send a product catalog message.
        
        Args:
            recipient_phone: Phone number
            product_ids: List of product.template IDs
            body: Optional message body
        """
        self.ensure_one()
        
        gateway = self.gateway_id
        
        # Build product items
        sections = [{
            "product_items": [
                {"product_retailer_id": str(pid)}
                for pid in product_ids[:30]  # Max 30 products
            ]
        }]
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone.replace("+", "").replace(" ", ""),
            "type": "interactive",
            "interactive": {
                "type": "product_list",
                "header": {
                    "type": "text",
                    "text": "Nossos Produtos",
                },
                "body": {
                    "text": body or "Confira nossos produtos:",
                },
                "action": {
                    "catalog_id": self.catalog_id,
                    "sections": sections,
                }
            }
        }
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            
            _logger.info("Product message sent to %s", recipient_phone)
            return response.json()
            
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to send product message: %s", e)
            raise UserError(_("Failed to send products: %s") % str(e)) from e

    def send_single_product(self, recipient_phone, product_id, body=None):
        """Send a single product card"""
        self.ensure_one()
        
        gateway = self.gateway_id
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone.replace("+", "").replace(" ", ""),
            "type": "interactive",
            "interactive": {
                "type": "product",
                "body": {
                    "text": body or "Confira este produto:",
                },
                "action": {
                    "catalog_id": self.catalog_id,
                    "product_retailer_id": str(product_id),
                }
            }
        }
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise UserError(_("Failed to send product: %s") % str(e)) from e
