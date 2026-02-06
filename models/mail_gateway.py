# Copyright 2022 Creu Blanca
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import requests
from werkzeug.urls import url_join

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BASE_URL = "https://graph.facebook.com/"


class MailGateway(models.Model):
    _inherit = "mail.gateway"

    whatsapp_security_key = fields.Char()
    gateway_type = fields.Selection(
        selection_add=[("whatsapp", "WhatsApp")], ondelete={"whatsapp": "cascade"}
    )
    
    # Provider Selection: Cloud API or Evolution API
    whatsapp_provider = fields.Selection(
        [
            ("cloud", "WhatsApp Business Cloud API (Official)"),
            ("evolution", "Evolution API (QR Code)"),
        ],
        default="cloud",
        string="Provider",
        help="Choose between official Meta Cloud API or Evolution API via QR code",
    )
    
    # Cloud API fields
    whatsapp_from_phone = fields.Char()
    whatsapp_version = fields.Selection(
        [
            ("19.0", "v19.0"),
            ("20.0", "v20.0"),
            ("21.0", "v21.0 (Recomendado)"),
            ("22.0", "v22.0"),
            ("23.0", "v23.0"),
            ("24.0", "v24.0 (Mais recente)"),
        ],
        default="21.0",
        required=True,
        string="API Version",
        help="Versão da API do WhatsApp Business Cloud. v21.0 é recomendada para estabilidade."
    )
    whatsapp_account_id = fields.Char()
    
    # Evolution API link
    evolution_instance_id = fields.Many2one(
        "mail.gateway.evolution",
        string="Evolution Instance",
        ondelete="set null",
        help="Evolution API instance for QR code based connection",
    )
    evolution_status = fields.Selection(
        related="evolution_instance_id.state",
        string="Evolution Status",
        readonly=True,
    )
    
    whatsapp_template_ids = fields.One2many("mail.whatsapp.template", "gateway_id")
    whatsapp_template_count = fields.Integer(compute="_compute_whatsapp_template_count")

    def action_open_evolution_config(self):
        """Open Evolution API configuration wizard"""
        self.ensure_one()
        if self.evolution_instance_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "mail.gateway.evolution",
                "res_id": self.evolution_instance_id.id,
                "view_mode": "form",
                "target": "current",
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "res_model": "mail.gateway.evolution",
                "view_mode": "form",
                "target": "current",
                "context": {"default_gateway_id": self.id},
            }


    @api.depends("whatsapp_template_ids")
    def _compute_whatsapp_template_count(self):
        for gateway in self:
            gateway.whatsapp_template_count = len(gateway.whatsapp_template_ids)

    def button_import_whatsapp_template(self):
        self.ensure_one()
        WhatsappTemplate = self.env["mail.whatsapp.template"]
        if not self.whatsapp_account_id:
            raise UserError(_("WhatsApp Account is required to import templates."))
        meta_info = {}
        template_url = url_join(
            BASE_URL,
            f"v{self.whatsapp_version}/{self.whatsapp_account_id}/message_templates",
        )
        try:
            meta_request = requests.get(
                template_url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            meta_request.raise_for_status()
            meta_info = meta_request.json()
        except Exception as err:
            raise UserError(str(err)) from err
        current_templates = WhatsappTemplate.with_context(active_test=False).search(
            [("gateway_id", "=", self.id)]
        )
        templates_by_id = {t.template_uid: t for t in current_templates}
        create_vals = []
        for template_data in meta_info.get("data", []):
            ws_template = templates_by_id.get(template_data["id"])
            if ws_template:
                ws_template.write(
                    WhatsappTemplate._prepare_values_to_import(self, template_data)
                )
            else:
                create_vals.append(
                    WhatsappTemplate._prepare_values_to_import(self, template_data)
                )
        WhatsappTemplate.create(create_vals)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("WathsApp Templates"),
                "type": "success",
                "message": _("Synchronization successfully."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
