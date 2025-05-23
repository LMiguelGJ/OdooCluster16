# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import json
import logging
import requests
from werkzeug.urls import url_join

from odoo import _, api, fields, models, tools
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
try:
    import firebase_admin
    from firebase_admin import messaging
    from firebase_admin import credentials
except ImportError:
    firebase_admin = None

try:
    from google.oauth2 import service_account
    from google.auth.transport import requests as google_requests
except ImportError:
    service_account = None


class SocialAccountPushNotifications(models.Model):
    _inherit = 'social.account'

    website_id = fields.Many2one('website', string="Website",
                                 help="This firebase configuration will only be used for the specified website", ondelete='cascade')
    firebase_use_own_account = fields.Boolean('Use your own Firebase account', related='website_id.firebase_use_own_account')
    firebase_project_id = fields.Char('Firebase Project ID', related='website_id.firebase_project_id')
    firebase_web_api_key = fields.Char('Firebase Web API Key', related='website_id.firebase_web_api_key')
    firebase_push_certificate_key = fields.Char('Firebase Push Certificate Key', related='website_id.firebase_push_certificate_key')
    firebase_sender_id = fields.Char('Firebase Sender ID', related='website_id.firebase_sender_id')
    firebase_admin_key_file = fields.Binary('Firebase Admin Key File', related='website_id.firebase_admin_key_file')

    notification_request_title = fields.Char('Notification Request Title', related='website_id.notification_request_title')
    notification_request_body = fields.Text('Notification Request Text', related='website_id.notification_request_body')
    notification_request_delay = fields.Integer('Notification Request Delay (seconds)', related='website_id.notification_request_delay')
    notification_request_icon = fields.Binary("Notification Request Icon", related='website_id.notification_request_icon')

    _sql_constraints = [('website_unique', 'unique(website_id)', 'There is already a configuration for this website.')]

    @api.ondelete(at_uninstall=False)
    def _unlink_except_push_notification_account(self):
        if not self.env.user.has_group('base.group_system') and any(account.website_id for account in self):
            raise UserError(_("You can't delete a Push Notification account."))

    def _init_firebase_app(self):
        """ Initialize the firebase library before usage.
        There is no actual way to tell if the app is already initialized or not.
        And we don't want to initialize it when the server starts because it could never be used.
        So we have to check for the ValueError that triggers if the app if already initialized and ignore it. """
        self.ensure_one()

        firebase_credentials = credentials.Certificate(json.loads(
            base64.b64decode(self.firebase_admin_key_file).decode())
        )
        try:
            firebase_admin.initialize_app(firebase_credentials, options={'httpTimeout': 10})
        except ValueError:
            # app already initialized
            pass

    def _firebase_send_message(self, data, visitors):
        visitors = visitors.filtered(lambda visitor: visitor.push_subscription_ids)
        if self.firebase_use_own_account:
            self._firebase_send_message_from_configuration(data, visitors)
        else:
            self._firebase_send_message_from_iap(data, visitors)

    def _firebase_send_message_from_configuration(self, data, visitors):
        """ This method now has a dual implementation to handle cases when the firebase_admin
        python library is not installed / not in the correct version.

        1. When firebase_admin is available:
           Sends messages by batch of 100 (max limit from firebase).
           Returns a tuple containing:
              - The matched website.visitors (search_read records).
              - A list of firebase_admin.messaging.BatchResponse to be handled by the caller.

        2. When firebase_admin is NOT available.
           Sends messages one by one using the firebase REST API.
           (Which is what firebase_admin does under the hood anyway)
           It requires a bearer token for authentication that we obtain using the google_auth library.
           Returns a tuple containing:
              - The matched website.visitors (search_read records).
              - An empty list. """

        if not visitors:
            return [], []

        if not self.firebase_admin_key_file:
            raise UserError(_("Firebase Admin Key File is missing from the configuration."))

        results = []
        tokens = visitors.mapped('push_subscription_ids.push_token')
        if firebase_admin and self._check_firebase_version():
            self._init_firebase_app()
            batch_size = 100

            for tokens_batch in tools.split_every(batch_size, tokens, piece_maker=list):
                firebase_message = messaging.MulticastMessage(
                    tokens=tokens_batch,
                    webpush=messaging.WebpushConfig(
                        notification=messaging.WebpushNotification(
                            title=data['title'],
                            body=data['body'],
                            icon=data['icon']),
                        data={'target_url': data['target_url']}))
                results.append(messaging.send_each_for_multicast(firebase_message))
        elif service_account:
            firebase_data = json.loads(
                base64.b64decode(self.firebase_admin_key_file).decode())
            firebase_credentials = service_account.Credentials.from_service_account_info(
                firebase_data,
                scopes=['https://www.googleapis.com/auth/firebase.messaging']
            )
            firebase_credentials.refresh(google_requests.Request())
            auth_token = firebase_credentials.token

            for token in tokens:
                requests.post(
                    f'https://fcm.googleapis.com/v1/projects/{firebase_data["project_id"]}/messages:send',
                    json={
                        'message': {
                            'token': token,
                            'notification': {
                                'title': data['title'],
                                'body': data['body'],
                                'image': data['icon']
                            },
                            'data': {
                                'target_url': data['target_url']
                            }
                        }
                    },
                    headers={'authorization': f'Bearer {auth_token}'},
                    timeout=5
                )
        else:
            raise UserError(_('You have to either install "firebase_admin>=2.17.0" or '
                              '"google_auth>=1.18.0" to be able to send push '
                              'notifications.'))

        return tokens, results

    def _firebase_send_message_from_iap(self, data, visitors):
        social_iap_endpoint = self.env['ir.config_parameter'].sudo().get_param(
            'social.social_iap_endpoint',
            self.env['social.media']._DEFAULT_SOCIAL_IAP_ENDPOINT
        )
        batch_size = 100

        tokens = visitors.mapped('push_subscription_ids.push_token')
        data.update({'db_uuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid')})
        for tokens_batch in tools.split_every(batch_size, tokens, piece_maker=list):
            batch_data = dict(data)
            batch_data['tokens'] = tokens_batch
            iap_tools.iap_jsonrpc(url_join(social_iap_endpoint, '/iap/social_push_notifications/firebase_send_message'), params=batch_data)

        return []

    def _check_firebase_version(self):
        """ Utility method to check that the installed firebase version has needed features. """
        version_compliant = firebase_admin and messaging and credentials \
            and hasattr(firebase_admin, 'initialize_app') \
            and hasattr(messaging, 'send_each_for_multicast')

        if not version_compliant:
            _logger.warning("""Your version of 'firebase_admin' is outdated.
                                 Please install version >=6.6.0.
                                 Falling back to native google-auth implementation.""")

        return version_compliant
