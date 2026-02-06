# Copyright 2024 Dixmit
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import hashlib
import hmac
import logging
import mimetypes
import traceback
from datetime import datetime
from io import StringIO

import requests
import requests_toolbelt

from odoo import _, models
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import html2plaintext

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

_logger = logging.getLogger(__name__)


class MailGatewayWhatsappService(models.AbstractModel):
    _inherit = "mail.gateway.abstract"
    _name = "mail.gateway.whatsapp"
    _description = "Whatsapp Gateway services"

    def _receive_get_update(self, bot_data, req, **kwargs):
        self._verify_update(bot_data, {})
        gateway = self.env["mail.gateway"].browse(bot_data["id"])
        if kwargs.get("hub.verify_token") != gateway.whatsapp_security_key:
            return None
        gateway.sudo().integrated_webhook_state = "integrated"
        response = request.make_response(kwargs.get("hub.challenge"))
        response.status_code = 200
        return response

    def _set_webhook(self, gateway):
        gateway.integrated_webhook_state = "pending"

    def _verify_update(self, bot_data, kwargs):
        signature = request.httprequest.headers.get("x-hub-signature-256")
        if not signature:
            return False
        if (
            "sha256=%s"
            % hmac.new(
                bot_data["webhook_secret"].encode(),
                request.httprequest.data,
                hashlib.sha256,
            ).hexdigest()
            != signature
        ):
            return False
        return True

    def send_read_receipt(self, gateway, message_id):
        """
        Send read receipt to WhatsApp to mark a message as read.
        
        This should be called when a user views a message in Odoo.
        
        Args:
            gateway: The mail.gateway record
            message_id: The WhatsApp message ID (wamid.xxx)
        
        Returns:
            bool: True if successful, False otherwise
        """
        if not message_id or not gateway:
            return False
        
        try:
            url = f"https://graph.facebook.com/v{gateway.whatsapp_version}/{gateway.whatsapp_from_phone}/messages"
            
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {gateway.token}"},
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
                timeout=10,
                proxies=self._get_proxies(),
            )
            response.raise_for_status()
            
            _logger.info("Read receipt sent for message: %s", message_id)
            return True
            
        except Exception as e:
            _logger.warning("Failed to send read receipt for %s: %s", message_id, e)
            return False

    def mark_messages_as_read(self, gateway, channel):
        """
        Mark all unread messages in a channel as read in WhatsApp.
        
        Should be called when user opens a WhatsApp conversation.
        
        Args:
            gateway: The mail.gateway record
            channel: The mail.channel record
        
        Returns:
            int: Number of messages marked as read
        """
        if not gateway or not channel:
            return 0
        
        # Find messages received from customer that haven't been marked as read
        Notification = self.env["mail.notification"].sudo()
        notifications = Notification.search([
            ("gateway_channel_id", "=", channel.id),
            ("gateway_message_id", "!=", False),
            # Only messages we received, not ones we sent
        ])
        
        count = 0
        for notification in notifications:
            message_id = notification.gateway_message_id
            if message_id and self.send_read_receipt(gateway, message_id):
                count += 1
        
        return count

    def _get_channel_vals(self, gateway, token, update):
        result = super()._get_channel_vals(gateway, token, update)
        for contact in update.get("contacts", []):
            if contact["wa_id"] == token:
                result["name"] = contact["profile"]["name"]
                continue
        return result

    def _receive_update(self, gateway, update):
        if update:
            for entry in update["entry"]:
                for change in entry["changes"]:
                    if change["field"] != "messages":
                        continue
                    value = change["value"]
                    
                    # Process incoming messages
                    for message in value.get("messages", []):
                        chat = self._get_channel(
                            gateway, message["from"], value, force_create=True
                        )
                        if not chat:
                            continue
                        self._process_update(chat, message, value)
                    
                    # Process message status updates (delivered, read, failed)
                    for status_info in value.get("statuses", []):
                        self._process_status_update(gateway, status_info)

    def _process_status_update(self, gateway, status_info):
        """
        Process WhatsApp message status updates (sent, delivered, read, failed).
        
        Status webhook payload example:
        {
            "id": "wamid.xxx",
            "status": "delivered",  # sent, delivered, read, failed
            "timestamp": "1234567890",
            "recipient_id": "34600000000",
            "errors": [{"code": 131047, "title": "...", "message": "..."}]
        }
        """
        whatsapp_message_id = status_info.get("id")
        status = status_info.get("status")
        recipient_id = status_info.get("recipient_id")
        timestamp = status_info.get("timestamp")
        
        if not whatsapp_message_id or not status:
            return
        
        # Convert timestamp to datetime
        from datetime import datetime
        status_datetime = None
        if timestamp:
            try:
                status_datetime = datetime.fromtimestamp(int(timestamp))
            except (ValueError, TypeError):
                status_datetime = datetime.now()
        
        # Find existing status record or notification
        MessageStatus = self.env["mail.whatsapp.message.status"].sudo()
        Notification = self.env["mail.notification"].sudo()
        
        # First try to find by notification gateway_message_id
        notification = Notification.search([
            ("gateway_message_id", "=", whatsapp_message_id),
        ], limit=1)
        
        # Find or create status record
        status_record = MessageStatus.search([
            ("whatsapp_message_id", "=", whatsapp_message_id),
        ], limit=1)
        
        if not status_record and notification:
            # Create new status record linked to the notification
            status_record = MessageStatus.create({
                "whatsapp_message_id": whatsapp_message_id,
                "notification_id": notification.id,
                "message_id": notification.mail_message_id.id,
                "recipient_id": recipient_id,
                "status": "sent",
                "sent_timestamp": status_datetime,
            })
        
        if status_record:
            # Prepare error info if status is failed
            error_info = None
            if status == "failed" and status_info.get("errors"):
                error = status_info["errors"][0] if status_info["errors"] else {}
                error_info = {
                    "code": str(error.get("code", "")),
                    "title": error.get("title", ""),
                    "message": error.get("message", ""),
                    "details": error.get("error_data", {}).get("details", ""),
                }
            
            # Update status
            status_record.update_status(status, status_datetime, error_info)
            
            _logger.info(
                "WhatsApp message %s status updated to: %s",
                whatsapp_message_id,
                status
            )
        else:
            _logger.warning(
                "Received status update for unknown message: %s",
                whatsapp_message_id
            )

    def _process_update(self, chat, message, value):
        chat.ensure_one()
        
        # Update 24-hour window timestamp when receiving a customer message
        chat._update_whatsapp_last_customer_message()
        
        body = ""
        attachments = []
        
        # Handle reactions
        if message.get("type") == "reaction":
            self._process_reaction(chat, message, value)
            return
        
        if message.get("text"):
            body = message.get("text").get("body")
        for key in ["image", "audio", "video", "document", "sticker"]:
            if message.get(key):
                image_id = message.get(key).get("id")
                if image_id:
                    image_info_request = requests.get(
                        "https://graph.facebook.com/v%s/%s"
                        % (
                            chat.gateway_id.whatsapp_version,
                            image_id,
                        ),
                        headers={
                            "Authorization": "Bearer %s" % chat.gateway_id.token,
                        },
                        timeout=10,
                        proxies=self._get_proxies(),
                    )
                    image_info_request.raise_for_status()
                    image_info = image_info_request.json()
                    image_url = image_info["url"]
                else:
                    image_url = message.get(key).get("url")
                if not image_url:
                    continue
                image_request = requests.get(
                    image_url,
                    headers={
                        "Authorization": "Bearer %s" % chat.gateway_id.token,
                    },
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                image_request.raise_for_status()
                attachments.append(
                    (
                        "{}{}".format(
                            image_id,
                            mimetypes.guess_extension(image_info["mime_type"]),
                        ),
                        image_request.content,
                    )
                )
        if message.get("location"):
            body += (
                '<a target="_blank" href="https://www.google.com/'
                'maps/search/?api=1&query=%s,%s">Location</a>'
                % (
                    message["location"]["latitude"],
                    message["location"]["longitude"],
                )
            )
        if message.get("contacts"):
            pass
        if len(body) > 0 or attachments:
            author = self._get_author(chat.gateway_id, value)
            new_message = chat.message_post(
                body=body,
                author_id=author and author._name == "res.partner" and author.id,
                gateway_type="whatsapp",
                date=datetime.fromtimestamp(int(message["timestamp"])),
                # message_id=update.message.message_id,
                subtype_xmlid="mail.mt_comment",
                message_type="comment",
                attachments=attachments,
            )
            self._post_process_message(new_message, chat)
            related_message_id = message.get("context", {}).get("id", False)
            if related_message_id:
                related_message = (
                    self.env["mail.notification"]
                    .search(
                        [
                            ("gateway_channel_id", "=", chat.id),
                            ("gateway_message_id", "=", related_message_id),
                        ]
                    )
                    .mail_message_id
                )
                if related_message and related_message.gateway_message_id:
                    new_related_message = (
                        self.env[related_message.gateway_message_id.model]
                        .browse(related_message.gateway_message_id.res_id)
                        .message_post(
                            body=body,
                            author_id=author
                            and author._name == "res.partner"
                            and author.id,
                            gateway_type="whatsapp",
                            date=datetime.fromtimestamp(int(message["timestamp"])),
                            # message_id=update.message.message_id,
                            subtype_xmlid="mail.mt_comment",
                            message_type="comment",
                            attachments=attachments,
                        )
                    )
                    self._post_process_reply(related_message)
                    new_message.gateway_message_id = new_related_message

    def _process_reaction(self, chat, message, value):
        """
        Process WhatsApp reaction messages (emoji reactions to messages).
        
        Reaction payload example:
        {
            "type": "reaction",
            "reaction": {
                "message_id": "wamid.xxx",
                "emoji": "👍"  # or "" for removing reaction
            }
        }
        """
        reaction_data = message.get("reaction", {})
        target_message_id = reaction_data.get("message_id")
        emoji = reaction_data.get("emoji", "")
        
        if not target_message_id:
            return
        
        # Find the message being reacted to
        notification = self.env["mail.notification"].search([
            ("gateway_channel_id", "=", chat.id),
            ("gateway_message_id", "=", target_message_id),
        ], limit=1)
        
        if notification and notification.mail_message_id:
            target_mail_message = notification.mail_message_id
            author = self._get_author(chat.gateway_id, value)
            
            if emoji:
                # Add reaction - post a note about the reaction
                reaction_body = f"<span class='o_mail_notification'>Reacted with {emoji}</span>"
                _logger.info(
                    "WhatsApp reaction %s added to message %s by %s",
                    emoji,
                    target_message_id,
                    author.name if author else "Unknown"
                )
                
                # Try to use Odoo's native reaction system if available (Odoo 16+)
                if hasattr(target_mail_message, 'reaction_ids'):
                    # Odoo 16+ has native reactions
                    try:
                        target_mail_message.with_context(
                            mail_create_nosubscribe=True
                        )._message_add_reaction(
                            content=emoji,
                            partner_id=author.id if author and author._name == "res.partner" else False,
                        )
                    except Exception as e:
                        _logger.warning("Could not add native reaction: %s", e)
            else:
                # Remove reaction (emoji is empty string)
                _logger.info(
                    "WhatsApp reaction removed from message %s",
                    target_message_id
                )
        else:
            _logger.warning(
                "Received reaction for unknown message: %s",
                target_message_id
            )

    def _send(
        self,
        gateway,
        record,
        auto_commit=False,
        raise_exception=False,
        parse_mode=False,
    ):
        message = False
        try:
            attachment_mimetype_map = self._get_whatsapp_mimetype_kind()
            for attachment in record.mail_message_id.attachment_ids:
                if attachment.mimetype not in attachment_mimetype_map:
                    raise UserError(_("Mimetype is not valid"))
                attachment_type = attachment_mimetype_map[attachment.mimetype]
                m = requests_toolbelt.multipart.encoder.MultipartEncoder(
                    fields={
                        "file": (
                            attachment.name,
                            attachment.raw,
                            attachment.mimetype,
                        ),
                        "messaging_product": "whatsapp",
                        # "type": attachment_type
                    },
                )

                response = requests.post(
                    "https://graph.facebook.com/v%s/%s/media"
                    % (
                        gateway.whatsapp_version,
                        gateway.whatsapp_from_phone,
                    ),
                    headers={
                        "Authorization": "Bearer %s" % gateway.token,
                        "content-type": m.content_type,
                    },
                    data=m,
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                response.raise_for_status()
                response = requests.post(
                    "https://graph.facebook.com/v%s/%s/messages"
                    % (
                        gateway.whatsapp_version,
                        gateway.whatsapp_from_phone,
                    ),
                    headers={"Authorization": "Bearer %s" % gateway.token},
                    json=self._send_payload(
                        record.gateway_channel_id,
                        media_id=response.json()["id"],
                        media_type=attachment_type,
                        media_name=attachment.name,
                    ),
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                response.raise_for_status()
                message = response.json()
            body = self._get_message_body(record)
            if body:
                response = requests.post(
                    "https://graph.facebook.com/v%s/%s/messages"
                    % (
                        gateway.whatsapp_version,
                        gateway.whatsapp_from_phone,
                    ),
                    headers={"Authorization": "Bearer %s" % gateway.token},
                    json=self._send_payload(record.gateway_channel_id, body=body),
                    timeout=10,
                    proxies=self._get_proxies(),
                )
                response.raise_for_status()
                message = response.json()
        except Exception as exc:
            buff = StringIO()
            traceback.print_exc(file=buff)
            _logger.error(buff.getvalue())
            if raise_exception:
                raise MailDeliveryException(
                    _("Unable to send the whatsapp message")
                ) from exc
            else:
                _logger.warning(
                    "Issue sending message with id {}: {}".format(record.id, exc)
                )
                record.sudo().write(
                    {"notification_status": "exception", "failure_reason": exc}
                )
        if message:
            record.sudo().write(
                {
                    "notification_status": "sent",
                    "failure_reason": False,
                    "gateway_message_id": message["messages"][0]["id"],
                }
            )
        if auto_commit is True:
            # pylint: disable=invalid-commit
            self.env.cr.commit()

    def _send_payload(
        self, channel, body=False, media_id=False, media_type=False, media_name=False
    ):
        whatsapp_template = self.env["mail.whatsapp.template"]
        if self.env.context.get("whatsapp_template_id"):
            whatsapp_template = self.env["mail.whatsapp.template"].browse(
                self.env.context.get("whatsapp_template_id")
            )
        if body:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": channel.gateway_channel_token,
            }
            if whatsapp_template:
                payload.update(
                    {
                        "type": "template",
                        "template": {
                            "name": whatsapp_template.template_name,
                            "language": {"code": whatsapp_template.language},
                        },
                    }
                )
            else:
                payload.update(
                    {
                        "type": "text",
                        "text": {"preview_url": False, "body": html2plaintext(body)},
                    }
                )
            return payload
        if media_id:
            media_data = {"id": media_id}
            if media_type == "document":
                media_data["filename"] = media_name
            return {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": channel.gateway_channel_token,
                "type": media_type,
                media_type: media_data,
            }

    def _get_whatsapp_mimetype_kind(self):
        return {
            "text/plain": "document",
            "application/pdf": "document",
            "application/vnd.ms-powerpoint": "document",
            "application/msword": "document",
            "application/vnd.ms-excel": "document",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document": "document",
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation": "document",
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet": "document",
            "audio/aac": "audio",
            "audio/mp4": "audio",
            "audio/mpeg": "audio",
            "audio/amr": "audio",
            "audio/ogg": "audio",
            "image/jpeg": "image",
            "image/png": "image",
            "video/mp4": "video",
            "video/3gp": "video",
            "image/webp": "sticker",
        }

    def _get_author(self, gateway, update):
        author_id = update.get("messages")[0].get("from")
        if author_id:
            gateway_partner = self.env["res.partner.gateway.channel"].search(
                [
                    ("gateway_id", "=", gateway.id),
                    ("gateway_token", "=", str(author_id)),
                ]
            )
            if gateway_partner:
                return gateway_partner.partner_id
            partner = self.env["res.partner"].search(
                [("phone_sanitized", "=", "+" + str(author_id))]
            )
            if partner:
                self.env["res.partner.gateway.channel"].create(
                    {
                        "name": gateway.name,
                        "partner_id": partner.id,
                        "gateway_id": gateway.id,
                        "gateway_token": str(author_id),
                    }
                )
                return partner
            guest = self.env["mail.guest"].search(
                [
                    ("gateway_id", "=", gateway.id),
                    ("gateway_token", "=", str(author_id)),
                ]
            )
            if guest:
                return guest
            author_vals = self._get_author_vals(gateway, author_id, update)
            if author_vals:
                return self.env["mail.guest"].create(author_vals)

        return False

    def _get_author_vals(self, gateway, author_id, update):
        for contact in update.get("contacts", []):
            if contact["wa_id"] == author_id:
                return {
                    "name": contact.get("profile", {}).get("name", "Anonymous"),
                    "gateway_id": gateway.id,
                    "gateway_token": str(author_id),
                }

    def _get_proxies(self):
        # This hook has been created in order to add a proxy if needed.
        # By default, it does nothing.
        return {}
