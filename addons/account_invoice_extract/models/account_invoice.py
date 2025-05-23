# -*- coding: utf-8 -*-

from psycopg2 import IntegrityError, OperationalError

from odoo import api, fields, models, _, _lt, Command
from odoo.addons.iap.tools import iap_tools
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, mute_logger
from odoo.tools.misc import clean_context, formatLang

import logging
import re
import json
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)

PARTNER_AUTOCOMPLETE_ENDPOINT = 'https://partner-autocomplete.odoo.com'
EXTRACT_ENDPOINT = 'https://iap-extract.odoo.com'
CLIENT_OCR_VERSION = 120

# list of result id that can be sent by iap-extract
SUCCESS = 0
NOT_READY = 1
ERROR_INTERNAL = 2
ERROR_NOT_ENOUGH_CREDIT = 3
ERROR_DOCUMENT_NOT_FOUND = 4
ERROR_NO_DOCUMENT_NAME = 5
ERROR_UNSUPPORTED_IMAGE_FORMAT = 6
ERROR_FILE_NAMES_NOT_MATCHING = 7
ERROR_NO_CONNECTION = 8
ERROR_SERVER_IN_MAINTENANCE = 9
ERROR_PASSWORD_PROTECTED = 10
ERROR_TOO_MANY_PAGES = 11
ERROR_INVALID_ACCOUNT_TOKEN = 12
ERROR_UNSUPPORTED_IMAGE_SIZE = 14
ERROR_NO_PAGE_COUNT = 15
ERROR_CONVERSION_PDF2IMAGE = 16

ERROR_MESSAGES = {
    ERROR_INTERNAL: _lt("An error occurred"),
    ERROR_DOCUMENT_NOT_FOUND: _lt("The document could not be found"),
    ERROR_NO_DOCUMENT_NAME: _lt("No document name provided"),
    ERROR_UNSUPPORTED_IMAGE_FORMAT: _lt("Unsupported image format"),
    ERROR_FILE_NAMES_NOT_MATCHING: _lt("You must send the same quantity of documents and file names"),
    ERROR_NO_CONNECTION: _lt("Server not available. Please retry later"),
    ERROR_SERVER_IN_MAINTENANCE: _lt("Server is currently under maintenance. Please retry later"),
    ERROR_PASSWORD_PROTECTED: _lt("Your PDF file is protected by a password. The OCR can't extract data from it"),
    ERROR_TOO_MANY_PAGES: _lt("Your invoice is too heavy to be processed by the OCR. Try to reduce the number of pages and avoid pages with too many text"),
    ERROR_INVALID_ACCOUNT_TOKEN: _lt("The 'invoice_ocr' IAP account token is invalid. Please delete it to let Odoo generate a new one or fill it with a valid token."),
    ERROR_UNSUPPORTED_IMAGE_SIZE: _lt("The document has been rejected because it is too small"),
    ERROR_NO_PAGE_COUNT: _lt("Invalid PDF (Unable to get page count)"),
    ERROR_CONVERSION_PDF2IMAGE: _lt("Invalid PDF (Conversion error)"),
}


class AccountInvoiceExtractionWords(models.Model):
    _name = "account.invoice_extract.words"
    _description = "Extracted words from invoice scan"

    invoice_id = fields.Many2one("account.move", required=True, ondelete='cascade', index=True, string="Invoice")
    field = fields.Char()

    ocr_selected = fields.Boolean()
    user_selected = fields.Boolean()
    word_text = fields.Char()
    word_page = fields.Integer()
    word_box_midX = fields.Float()
    word_box_midY = fields.Float()
    word_box_width = fields.Float()
    word_box_height = fields.Float()
    word_box_angle = fields.Float()


class AccountMove(models.Model):
    _inherit = ['account.move']

    @api.depends('extract_status_code')
    def _compute_error_message(self):
        for record in self:
            if record.extract_status_code not in (SUCCESS, NOT_READY):
                record.extract_error_message = str(ERROR_MESSAGES.get(record.extract_status_code, ERROR_MESSAGES[ERROR_INTERNAL]))
            else:
                record.extract_error_message = ''

    def _compute_can_show_send_resend(self):
        self.ensure_one()
        return (
            self.state == 'draft'
            and self.message_main_attachment_id
            and self.is_invoice()
            and not self._check_digitalization_mode(self.company_id, self.move_type, 'no_send')
        )

    @api.depends('state', 'extract_state', 'message_main_attachment_id')
    def _compute_show_resend_button(self):
        for record in self:
            record.extract_can_show_resend_button = record._compute_can_show_send_resend()
            if record.extract_state not in ['error_status', 'not_enough_credit']:
                record.extract_can_show_resend_button = False

    @api.depends('state', 'extract_state', 'message_main_attachment_id')
    def _compute_show_send_button(self):
        for record in self:
            record.extract_can_show_send_button = record._compute_can_show_send_resend()
            if record.extract_state not in ['no_extract_requested']:
                record.extract_can_show_send_button = False

    @api.depends(
        'state',
        'extract_state',
        'move_type',
        'company_id.extract_in_invoice_digitalization_mode',
        'company_id.extract_out_invoice_digitalization_mode',
    )
    def _compute_show_banners(self):
        for record in self:
            record.extract_can_show_banners = (
                record.is_invoice() and
                record.state == 'draft' and
                (
                    (record.is_purchase_document() and record.company_id.extract_in_invoice_digitalization_mode != 'no_send') or
                    (record.is_sale_document() and record.company_id.extract_out_invoice_digitalization_mode != 'no_send')
                )
            )

    extract_state = fields.Selection([('no_extract_requested', 'No extract requested'),
                                      ('not_enough_credit', 'Not enough credit'),
                                      ('error_status', 'An error occurred'),
                                      ('waiting_upload', 'Waiting upload'),
                                      ('waiting_extraction', 'Waiting extraction'),
                                      ('extract_not_ready', 'waiting extraction, but it is not ready'),
                                      ('waiting_validation', 'Waiting validation'),
                                      ('to_validate', 'To validate'),
                                      ('done', 'Completed flow')],
                                     'Extract state', default='no_extract_requested', required=True, copy=False)
    extract_status_code = fields.Integer("Status code", copy=False)
    extract_error_message = fields.Text("Error message", compute=_compute_error_message)
    extract_remote_id = fields.Integer("Id of the request to IAP-OCR", default="-1", copy=False, readonly=True)
    extract_word_ids = fields.One2many("account.invoice_extract.words", inverse_name="invoice_id", copy=False)
    extract_attachment_id = fields.Many2one('ir.attachment', readonly=True, ondelete='set null', copy=False, index='btree_not_null')

    extract_can_show_resend_button = fields.Boolean("Can show the ocr resend button", compute=_compute_show_resend_button)
    extract_can_show_send_button = fields.Boolean("Can show the ocr send button", compute=_compute_show_send_button)
    extract_can_show_banners = fields.Boolean("Can show the ocr banners", compute=_compute_show_banners)

    def action_reload_ai_data(self):
        try:
            with self._get_edi_creation() as move_form:
                # The OCR doesn't overwrite the fields, so it's necessary to reset them
                move_form.partner_id = False
                move_form.invoice_date = False
                move_form.invoice_payment_term_id = False
                move_form.invoice_date_due = False

                if move_form.is_purchase_document():
                    move_form.ref = False
                elif move_form.is_sale_document() and move_form.quick_edit_mode:
                    move_form.name = False

                move_form.payment_reference = False
                move_form.currency_id = move_form.company_currency_id
                move_form.invoice_line_ids = [Command.clear()]
            self._check_status(force_write=True)
        except Exception as e:
            _logger.warning("Error while reloading AI data on account.move %d: %s", self.id, e)
            raise AccessError(_lt("Couldn't reload AI data."))

    def _get_iap_account(self):
        return self.env['iap.account'].with_context(allowed_company_ids=[self.company_id.id]).get('invoice_ocr')

    def _domain_company(self):
        return ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)]

    @api.model
    def _contact_iap_extract(self, local_endpoint, params):
        params['version'] = CLIENT_OCR_VERSION
        endpoint = self.env['ir.config_parameter'].sudo().get_param('account_invoice_extract_endpoint', EXTRACT_ENDPOINT)
        return iap_tools.iap_jsonrpc(endpoint + local_endpoint, params=params)

    @api.model
    def _contact_iap_partner_autocomplete(self, local_endpoint, params):
        return iap_tools.iap_jsonrpc(PARTNER_AUTOCOMPLETE_ENDPOINT + local_endpoint, params=params)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        return super(AccountMove, self.with_context(from_alias=True)).message_new(msg_dict, custom_values=custom_values)

    def _check_digitalization_mode(self, company, document_type, mode):
        if document_type in self.get_purchase_types():
            return company.extract_in_invoice_digitalization_mode == mode
        elif document_type in self.get_sale_types():
            return company.extract_out_invoice_digitalization_mode == mode

    def _needs_auto_extract(self):
        """ Returns `True` if the document should be automatically sent to the extraction server"""
        return (
            self.extract_state == "no_extract_requested"
            and
            (
                self._check_digitalization_mode(self.company_id, self.move_type, 'auto_send')
                and
                (
                    self.is_purchase_document()
                    # In the case of OUT invoices, it is only automatically sent for extraction if it comes from
                    # the email alias. This is indicated by the presence of the key 'from_alias' in the context
                    or self._context.get('from_alias')
                )
            )
        )

    def _ocr_create_document_from_attachment(self, attachment):
        invoice = self.env['account.move'].create({})
        invoice.message_main_attachment_id = attachment
        invoice.action_manual_send_for_digitization()
        return invoice

    def _ocr_update_invoice_from_attachment(self, attachment, invoice):
        invoice.action_manual_send_for_digitization()
        return invoice

    def _get_create_document_from_attachment_decoders(self):
        # OVERRIDE
        res = super()._get_create_document_from_attachment_decoders()
        if self._check_digitalization_mode(self.env.company, self._context.get('default_move_type'), 'auto_send'):
            res.append((20, self._ocr_create_document_from_attachment))
        return res

    def _get_update_invoice_from_attachment_decoders(self, invoice):
        # OVERRIDE
        res = super()._get_update_invoice_from_attachment_decoders(invoice)
        if invoice._needs_auto_extract():
            res.append((20, self._ocr_update_invoice_from_attachment))
        return res

    def action_manual_send_for_digitization(self):
        for rec in self:
            rec.env['iap.account']._send_iap_bus_notification(
                service_name='invoice_ocr',
                title=_lt("Bill is being Digitized"))
        self.extract_state = 'waiting_upload'
        self.env.ref('account_invoice_extract.ir_cron_ocr_parse')._trigger()

    @api.model
    def _cron_parse(self):
        for rec in self.search([('extract_state', '=', 'waiting_upload')]):
            try:
                with self.env.cr.savepoint(flush=False):
                    rec.with_company(rec.company_id).retry_ocr()
                    # We handle the flush manually so that if an error occurs, e.g. a concurrent update error,
                    # the savepoint will be rollbacked when exiting the context manager
                    self.env.cr.flush()
                self.env.cr.commit()
            except (IntegrityError, OperationalError) as e:
                _logger.error("Couldn't upload %s with id %d: %s", rec._name, rec.id, str(e))

    def is_indian_taxes(self):
        l10n_in = self.env['ir.module.module'].search([('name', '=', 'l10n_in')])
        return self.company_id.country_id.code == "IN" and l10n_in and l10n_in.state == 'installed'

    def get_user_infos(self):
        user_infos = {
            'user_company_VAT': self.company_id.vat,
            'user_company_name': self.company_id.name,
            'user_company_country_code': self.company_id.country_id.code,
            'user_lang': self.env.user.lang,
            'user_email': self.env.user.email,
            'perspective': 'supplier' if self.is_sale_document() else 'client',
        }
        return user_infos

    def retry_ocr(self):
        """Retry to contact iap to submit the first attachment in the chatter"""
        self.ensure_one()
        if self._check_digitalization_mode(self.company_id, self.move_type, 'no_send'):
            return
        attachments = self.message_main_attachment_id
        if (
                attachments.exists() and
                self.is_invoice() and
                self.extract_state in ['no_extract_requested', 'waiting_upload', 'not_enough_credit', 'error_status']
        ):
            account_token = self._get_iap_account()
            user_infos = self.get_user_infos()
            #this line contact iap to create account if this is the first request. This allow iap to give free credits if the database is elligible
            self.env['iap.account'].get_credits('invoice_ocr')
            if not account_token.account_token:
                self.extract_state = 'error_status'
                self.extract_status_code = ERROR_INVALID_ACCOUNT_TOKEN
                return
            baseurl = self.get_base_url()
            webhook_url = f"{baseurl}/account_invoice_extract/request_done"
            params = {
                'account_token': account_token.account_token,
                'dbuuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid'),
                'documents': [x.datas.decode('utf-8') for x in attachments],
                'user_infos': user_infos,
                'webhook_url': webhook_url,
            }
            try:
                result = self._contact_iap_extract('/api/extract/invoice/2/parse', params)
                self.extract_status_code = result['status_code']
                if result['status_code'] == SUCCESS:
                    if self.env['ir.config_parameter'].sudo().get_param("account_invoice_extract.already_notified", True):
                        self.env['ir.config_parameter'].sudo().set_param("account_invoice_extract.already_notified", False)
                    self.extract_state = 'waiting_extraction'
                    self.extract_remote_id = result['document_token']
                    self.extract_attachment_id = attachments
                elif result['status_code'] == ERROR_NOT_ENOUGH_CREDIT:
                    self.send_no_credit_notification()
                    self.extract_state = 'not_enough_credit'
                else:
                    self.extract_state = 'error_status'
                    _logger.warning('There was an issue while doing the OCR operation on this file. Error: -1')

            except AccessError:
                self.extract_state = 'error_status'
                self.extract_status_code = ERROR_NO_CONNECTION

    def send_no_credit_notification(self):
        """
        Notify about the number of credit.
        In order to avoid to spam people each hour, an ir.config_parameter is set
        """
        #If we don't find the config parameter, we consider it True, because we don't want to notify if no credits has been bought earlier.
        already_notified = self.env['ir.config_parameter'].sudo().get_param("account_invoice_extract.already_notified", True)
        if already_notified:
            return
        try:
            mail_template = self.env.ref('account_invoice_extract.account_invoice_extract_no_credit')
        except ValueError:
            #if the mail template has not been created by an upgrade of the module
            return
        iap_account = self._get_iap_account()
        if iap_account:
            # Get the email address of the creators of the records
            res = self.env['res.users'].search_read([('id', '=', 2)], ['email'])
            if res:
                email_values = {
                    'email_to': res[0]['email']
                }
                mail_template.send_mail(iap_account.id, force_send=True, email_values=email_values)
                self.env['ir.config_parameter'].sudo().set_param("account_invoice_extract.already_notified", True)

    def get_validation(self, field):
        """
        return the text or box corresponding to the choice of the user.
        If the user selected a box on the document, we return this box,
        but if he entered the text of the field manually, we return only the text, as we
        don't know which box is the right one (if it exists)
        """
        text_to_send = {}
        if field == "total":
            text_to_send["content"] = self.amount_total
        elif field == "subtotal":
            text_to_send["content"] = self.amount_untaxed
        elif field == "global_taxes_amount":
            text_to_send["content"] = self.amount_tax
        elif field == "global_taxes":
            text_to_send["content"] = [{
                'amount': line.debit,
                'tax_amount': line.tax_line_id.amount,
                'tax_amount_type': line.tax_line_id.amount_type,
                'tax_price_include': line.tax_line_id.price_include} for line in self.line_ids.filtered('tax_repartition_line_id')]
        elif field == "date":
            text_to_send["content"] = str(self.invoice_date) if self.invoice_date else False
        elif field == "due_date":
            text_to_send["content"] = str(self.invoice_date_due) if self.invoice_date_due else False
        elif field == "invoice_id":
            if self.is_purchase_document():
                text_to_send["content"] = self.ref
            else:
                text_to_send["content"] = self.name
        elif field == "partner":
            text_to_send["content"] = self.partner_id.name
        elif field == "VAT_Number":
            text_to_send["content"] = self.partner_id.vat
        elif field == "currency":
            text_to_send["content"] = self.currency_id.name
        elif field == "payment_ref":
            text_to_send["content"] = self.payment_reference
        elif field == "iban":
            text_to_send["content"] = self.partner_bank_id.acc_number if self.partner_bank_id else False
        elif field == "SWIFT_code":
            text_to_send["content"] = self.partner_bank_id.bank_bic if self.partner_bank_id else False
        elif field == "invoice_lines":
            text_to_send = {'lines': []}
            for il in self.invoice_line_ids:
                line = {
                    "description": il.name,
                    "quantity": il.quantity,
                    "unit_price": il.price_unit,
                    "product": il.product_id.id,
                    "taxes_amount": round(il.price_total - il.price_subtotal, 2),
                    "taxes": [{
                        'amount': tax.amount,
                        'type': tax.amount_type,
                        'price_include': tax.price_include} for tax in il.tax_ids],
                    "subtotal": il.price_subtotal,
                    "total": il.price_total
                }
                text_to_send['lines'].append(line)
            if self.is_indian_taxes():
                lines = text_to_send['lines']
                for index, line in enumerate(text_to_send['lines']):
                    for tax in line['taxes']:
                        taxes = []
                        if tax['type'] == 'group':
                            taxes.extend([{
                                'amount': tax['amount'] / 2,
                                'type': 'percent',
                                'price_include': tax['price_include']
                            } for _ in range(2)])
                        else:
                            taxes.append(tax)
                        lines[index]['taxes'] = taxes
                text_to_send['lines'] = lines
        else:
            return None

        user_selected_box = self.env['account.invoice_extract.words'].search([
            ('invoice_id', '=', self.id),
            ('field', '=', field),
            ('user_selected', '=', True),
            ('ocr_selected', '=', False),
        ])
        if user_selected_box and user_selected_box.word_text == text_to_send['content']:
            text_to_send['box'] = [
                user_selected_box.word_text,
                user_selected_box.word_page,
                user_selected_box.word_box_midX,
                user_selected_box.word_box_midY,
                user_selected_box.word_box_width,
                user_selected_box.word_box_height,
                user_selected_box.word_box_angle,
            ]
        return text_to_send

    @api.model
    def _cron_validate(self):
        inv_to_validate = self.search([('extract_state', '=', 'to_validate'), ('state', '=', 'posted')])

        if inv_to_validate:
            for record in inv_to_validate:
                account = record._get_iap_account()
                values = {
                    'total': record.get_validation('total'),
                    'subtotal': record.get_validation('subtotal'),
                    'global_taxes': record.get_validation('global_taxes'),
                    'global_taxes_amount': record.get_validation('global_taxes_amount'),
                    'date': record.get_validation('date'),
                    'due_date': record.get_validation('due_date'),
                    'invoice_id': record.get_validation('invoice_id'),
                    'partner': record.get_validation('partner'),
                    'VAT_Number': record.get_validation('VAT_Number'),
                    'currency': record.get_validation('currency'),
                    'payment_ref': record.get_validation('payment_ref'),
                    'iban': record.get_validation('iban'),
                    'SWIFT_code': record.get_validation('SWIFT_code'),
                    'merged_lines': self.env.company.extract_single_line_per_tax,
                    'invoice_lines': record.get_validation('invoice_lines')
                }
                params = {
                    'values': values,
                    'document_token': record.extract_remote_id,
                    'account_token': account.account_token,
                }
                try:
                    self._contact_iap_extract('/api/extract/invoice/2/validate', params=params)
                except AccessError:
                    pass

        inv_to_validate.extract_state = 'done'
        inv_to_validate.mapped('extract_word_ids').unlink()  # We don't need word data anymore, we can delete them

    def _post(self, soft=True):
        # OVERRIDE
        # On the validation of an invoice, send the different corrected fields to iap to improve the ocr algorithm.
        posted = super()._post(soft)

        moves_to_validate = posted.filtered(lambda m: m.extract_state == 'waiting_validation')
        moves_to_validate.extract_state = 'to_validate'

        if moves_to_validate:
            ocr_trigger_datetime = fields.Datetime.now() + relativedelta(minutes=self.env.context.get('ocr_trigger_delta', 0))
            self.env.ref('account_invoice_extract.ir_cron_ocr_validate')._trigger(at=ocr_trigger_datetime)

        return posted

    def get_boxes(self):
        return [{
            "id": data.id,
            "feature": data.field,
            "text": data.word_text,
            "ocr_selected": data.ocr_selected,
            "user_selected": data.user_selected,
            "page": data.word_page,
            "box_midX": data.word_box_midX,
            "box_midY": data.word_box_midY,
            "box_width": data.word_box_width,
            "box_height": data.word_box_height,
            "box_angle": data.word_box_angle} for data in self.extract_word_ids]

    def set_user_selected_box(self, id):
        """Set the selected box for a feature. The id of the box indicates the concerned feature.
        The method returns the text that can be set in the view (possibly different of the text in the file)"""
        self.ensure_one()
        word = self.env["account.invoice_extract.words"].browse(int(id))
        to_unselect = self.env["account.invoice_extract.words"].search([("invoice_id", "=", self.id), ("field", "=", word.field), ("user_selected", "=", True)])
        for box in to_unselect:
            box.user_selected = False

        word.user_selected = True
        if word.field == "currency":
            text = word.word_text
            currency = None
            currencies = self.env["res.currency"].search([])
            for curr in currencies:
                if text == curr.currency_unit_label:
                    currency = curr
                if text == curr.name or text == curr.symbol:
                    currency = curr
            if currency:
                return currency.id
            return self.currency_id.id
        if word.field == "VAT_Number":
            partner_vat = False
            if word.word_text != "":
                partner_vat = self.find_partner_id_with_vat(word.word_text)
            if partner_vat:
                return partner_vat.id
            else:
                vat = word.word_text
                partner = self._create_supplier_from_vat(vat)
                return partner.id if partner else False

        if word.field == "supplier":
            return self.find_partner_id_with_name(word.word_text)
        return word.word_text

    def find_partner_id_with_vat(self, vat_number_ocr):
        partner_vat = self.env["res.partner"].search([("vat", "=ilike", vat_number_ocr), *self._domain_company()], limit=1)
        if not partner_vat:
            partner_vat = self.env["res.partner"].search([("vat", "=ilike", vat_number_ocr[2:]), *self._domain_company()], limit=1)
        if not partner_vat:
            for partner in self.env["res.partner"].search([("vat", "!=", False), *self._domain_company()], limit=1000):
                vat = partner.vat.upper()
                vat_cleaned = vat.replace("BTW", "").replace("MWST", "").replace("ABN", "")
                vat_cleaned = re.sub(r'[^A-Z0-9]', '', vat_cleaned)
                if vat_cleaned == vat_number_ocr or vat_cleaned == vat_number_ocr[2:]:
                    partner_vat = partner
                    break
        return partner_vat

    def _create_supplier_from_vat(self, vat_number_ocr):
        try:
            response, error = self.env['iap.autocomplete.api']._request_partner_autocomplete(
                action='enrich',
                params={'vat': vat_number_ocr},
            )
            if error:
                raise Exception(error)
            if 'credit_error' in response and response['credit_error']:
                _logger.warning("Credit error on partner_autocomplete call")
        except KeyError:
            _logger.warning("Partner autocomplete isn't installed, supplier creation from VAT is disabled")
            return False
        except Exception as exception:
            _logger.error('Check VAT error: %s' % str(exception))
            return False

        if response and response.get('company_data'):
            country_id = self.env['res.country'].search([('code', '=', response.get('company_data').get('country_code',''))])
            resp_values = response.get('company_data')

            values = {field: resp_values[field] for field in ('name', 'vat', 'street', 'city', 'zip', 'phone', 'email', 'partner_gid') if field in resp_values}
            values['is_company'] = True

            if 'bank_ids' in resp_values:
                values['bank_ids'] = [(0, 0, vals) for vals in resp_values['bank_ids']]

            if country_id:
                values['country_id'] = country_id.id
                state_id = self.env['res.country.state'].search([
                    ('name', '=', response.get('company_data').get('state_name','')),
                    ('country_id', '=', country_id.id),
                ], limit = 1)
                if state_id:
                    values['state_id'] = state_id.id

            new_partner = self.env["res.partner"].with_context(clean_context(self.env.context)).create(values)
            return new_partner
        return False

    def find_partner_id_with_name(self, partner_name):
        if not partner_name:
            return 0

        partner = self.env["res.partner"].search([("name", "=", partner_name), *self._domain_company()], order='supplier_rank desc', limit=1)
        if partner:
            return partner.id if partner.id != self.company_id.partner_id.id else 0

        self.env.cr.execute("""
            SELECT id, name
            FROM res_partner
            WHERE active = true
              AND supplier_rank > 0
              AND name IS NOT NULL
              AND (company_id IS NULL OR company_id = %s)
        """, [self.company_id.id])

        partners_dict = {name.lower().replace('-', ' '): partner_id for partner_id, name in self.env.cr.fetchall()}
        partner_name = partner_name.lower().strip()

        partners = {}
        for single_word in [word for word in re.findall(r"\w+", partner_name) if len(word) >= 3]:
            partners_matched = [partner for partner in partners_dict if single_word in partner.split()]
            if len(partners_matched) == 1:
                partner = partners_matched[0]
                partners[partner] = partners[partner] + 1 if partner in partners else 1

        if partners:
            sorted_partners = sorted(partners, key=partners.get, reverse=True)
            if len(sorted_partners) == 1 or partners[sorted_partners[0]] != partners[sorted_partners[1]]:
                partner = sorted_partners[0]
                if partners_dict[partner] != self.company_id.partner_id.id:
                    return partners_dict[partner]
        return 0

    def _get_partner(self, ocr_results):
        supplier_ocr = ocr_results['supplier']['selected_value']['content'] if 'supplier' in ocr_results else ""
        client_ocr = ocr_results['client']['selected_value']['content'] if 'client' in ocr_results else ""
        vat_number_ocr = ocr_results['VAT_Number']['selected_value']['content'] if 'VAT_Number' in ocr_results else ""
        iban_ocr = ocr_results['iban']['selected_value']['content'] if 'iban' in ocr_results else ""

        # Try to find the partner with the VAT number
        if vat_number_ocr:
            partner_vat = self.find_partner_id_with_vat(vat_number_ocr)
            if partner_vat:
                return partner_vat, False

        # Try to find the partner with its IBAN
        if self.is_purchase_document() and iban_ocr:
            bank_account = self.env['res.partner.bank'].search([('acc_number', '=ilike', iban_ocr), *self._domain_company()])
            if len(bank_account) == 1:
                return bank_account.partner_id, False

        # Try to find the partner by its name
        partner_id = self.find_partner_id_with_name(client_ocr if self.is_sale_document() else supplier_ocr)
        if partner_id != 0:
            return self.env["res.partner"].browse(partner_id), False

        # Create a partner from the VAT number
        if vat_number_ocr:
            created_supplier = self._create_supplier_from_vat(vat_number_ocr)
            if created_supplier:
                return created_supplier, True
        return False, False

    def _get_taxes_record(self, taxes_ocr, taxes_type_ocr):
        """
        Find taxes records to use from the taxes detected for an invoice line.
        """
        taxes_found = self.env['account.tax']
        type_tax_use = 'purchase' if self.is_purchase_document() else 'sale'
        if self.is_indian_taxes() and len(taxes_ocr) > 1:
            total_tax = sum(taxes_ocr)
            grouped_taxes_records = self.env['account.tax'].search([
                ('amount', '=', total_tax),
                ('amount_type', '=', 'group'),
                ('type_tax_use', '=', type_tax_use),
                *self._domain_company(),
            ])
            for grouped_tax in grouped_taxes_records:
                children_taxes = grouped_tax.children_tax_ids.mapped('amount')
                if set(taxes_ocr) == set(children_taxes):
                    return grouped_tax
        for (taxes, taxes_type) in zip(taxes_ocr, taxes_type_ocr):
            if taxes != 0.0:
                related_documents = self.env['account.move'].search([
                    ('state', '!=', 'draft'),
                    ('move_type', '=', self.move_type),
                    ('partner_id', '=', self.partner_id.id),
                    *self._domain_company(),
                ], limit=100, order='id desc')
                lines = related_documents.mapped('invoice_line_ids')
                taxes_ids = related_documents.mapped('invoice_line_ids.tax_ids')
                taxes_ids = taxes_ids.filtered(
                    lambda tax:
                        tax.active and
                        tax.amount == taxes and
                        tax.amount_type == taxes_type and
                        tax.type_tax_use == type_tax_use
                )
                taxes_by_document = []
                for tax in taxes_ids:
                    taxes_by_document.append((tax, lines.filtered(lambda line: tax in line.tax_ids)))
                if len(taxes_by_document) != 0:
                    taxes_found |= max(taxes_by_document, key=lambda tax: len(tax[1]))[0]
                else:
                    tax_domain = [
                        ('amount', '=', taxes),
                        ('amount_type', '=', taxes_type),
                        ('type_tax_use', '=', type_tax_use),
                        *self._domain_company(),
                    ]
                    default_taxes = self.company_id.account_purchase_tax_id | self.company_id.account_sale_tax_id
                    matching_default_tax = default_taxes.filtered_domain(tax_domain)
                    if matching_default_tax:
                        taxes_found |= matching_default_tax
                    else:
                        taxes_records = self.env['account.tax'].search(tax_domain)
                        if taxes_records:
                            # prioritize taxes based on db setting
                            line_tax_type = self.env['ir.config_parameter'].sudo().get_param('account.show_line_subtotals_tax_selection')
                            taxes_records_setting_based = taxes_records.filtered(lambda r: not r.price_include if line_tax_type == 'tax_excluded' else r.price_include)
                            if taxes_records_setting_based:
                                taxes_record = taxes_records_setting_based[0]
                            else:
                                taxes_record = taxes_records[0]
                            taxes_found |= taxes_record
        return taxes_found

    def _get_currency(self, currency_ocr, partner_id):
        for comparison in ['=ilike', 'ilike']:
            possible_currencies = self.env["res.currency"].search([
                '|', '|',
                ('currency_unit_label', comparison, currency_ocr),
                ('name', comparison, currency_ocr),
                ('symbol', comparison, currency_ocr),
            ])
            if possible_currencies:
                break

        partner_last_invoice_currency = partner_id.invoice_ids[:1].currency_id
        if partner_last_invoice_currency in possible_currencies:
            return partner_last_invoice_currency
        if self.company_id.currency_id in possible_currencies:
            return self.company_id.currency_id
        return possible_currencies if len(possible_currencies) == 1 else None

    def _get_invoice_lines(self, ocr_results):
        """
        Get write values for invoice lines.
        """
        self.ensure_one()

        invoice_lines = ocr_results['invoice_lines'] if 'invoice_lines' in ocr_results else []
        subtotal_ocr = ocr_results['subtotal']['selected_value']['content'] if 'subtotal' in ocr_results else 0.0
        supplier_ocr = ocr_results['supplier']['selected_value']['content'] if 'supplier' in ocr_results else ""
        date_ocr = ocr_results['date']['selected_value']['content'] if 'date' in ocr_results else ""

        invoice_lines_to_create = []
        if self.company_id.extract_single_line_per_tax:
            merged_lines = {}
            for il in invoice_lines:
                total = il['total']['selected_value']['content'] if 'total' in il else 0.0
                subtotal = il['subtotal']['selected_value']['content'] if 'subtotal' in il else total
                taxes_ocr = [value['content'] for value in il['taxes']['selected_values']] if 'taxes' in il else []
                taxes_type_ocr = [value['amount_type'] if 'amount_type' in value else 'percent' for value in il['taxes']['selected_values']] if 'taxes' in il else []
                taxes_records = self._get_taxes_record(taxes_ocr, taxes_type_ocr)

                if not taxes_records and taxes_ocr:
                    taxes_ids = ('not found', *sorted(taxes_ocr))
                else:
                    taxes_ids = ('found', *sorted(taxes_records.ids))

                if taxes_ids not in merged_lines:
                    merged_lines[taxes_ids] = {'subtotal': subtotal}
                else:
                    merged_lines[taxes_ids]['subtotal'] += subtotal
                merged_lines[taxes_ids]['taxes_records'] = taxes_records

            # if there is only one line after aggregating the lines, use the total found by the ocr as it is less error-prone
            if len(merged_lines) == 1:
                merged_lines[list(merged_lines.keys())[0]]['subtotal'] = subtotal_ocr

            description_fields = []
            if supplier_ocr:
                description_fields.append(supplier_ocr)
            if date_ocr:
                description_fields.append(date_ocr.split()[0])
            description = ' - '.join(description_fields)

            for il in merged_lines.values():
                vals = {
                    'name': description,
                    'price_unit': il['subtotal'],
                    'quantity': 1.0,
                    'tax_ids': il['taxes_records'],
                }

                invoice_lines_to_create.append(vals)
        else:
            for il in invoice_lines:
                description = il['description']['selected_value']['content'] if 'description' in il else "/"
                total = il['total']['selected_value']['content'] if 'total' in il else 0.0
                subtotal = il['subtotal']['selected_value']['content'] if 'subtotal' in il else total
                unit_price = il['unit_price']['selected_value']['content'] if 'unit_price' in il else subtotal
                quantity = il['quantity']['selected_value']['content'] if 'quantity' in il else 1.0
                taxes_ocr = [value['content'] for value in il['taxes']['selected_values']] if 'taxes' in il else []
                taxes_type_ocr = [value['amount_type'] if 'amount_type' in value else 'percent' for value in il['taxes']['selected_values']] if 'taxes' in il else []

                vals = {
                    'name': description,
                    'price_unit': unit_price,
                    'quantity': quantity,
                    'tax_ids': self._get_taxes_record(taxes_ocr, taxes_type_ocr)
                }

                invoice_lines_to_create.append(vals)

        return invoice_lines_to_create

    @api.model
    def check_all_status(self):
        for record in self.search([('state', '=', 'draft'), ('extract_state', 'in', ['waiting_extraction', 'extract_not_ready'])]):
            try:
                with self.env.cr.savepoint():
                    record._check_status()
                self.env.cr.commit()
            except Exception as e:
                _logger.error("Couldn't check status of account.move with id %d: %s", record.id, str(e))

    def check_status(self):
        """contact iap to get the actual status of the ocr requests"""
        if any(rec.extract_state == 'waiting_upload' for rec in self):
            _logger.info("Manual trigger of the parse cron")
            try:
                self.env.ref('account_invoice_extract.ir_cron_ocr_parse')._try_lock()
                self.env.ref('account_invoice_extract.ir_cron_ocr_parse').sudo().method_direct_trigger()
            except UserError:
                _logger.warning("Lock acquiring failed, cron is already running")
                return

        records_to_update = self.filtered(lambda inv: inv.extract_state in ['waiting_extraction', 'extract_not_ready'] and inv.state == 'draft')

        for record in records_to_update:
            record._check_status()

        limit = max(0, 20 - len(records_to_update))
        if limit > 0:
            records_to_preupdate = self.search([('extract_state', 'in', ['waiting_extraction', 'extract_not_ready']), ('id', 'not in', records_to_update.ids), ('state', '=', 'draft')], limit=limit)
            for record in records_to_preupdate:
                try:
                    with self.env.cr.savepoint():
                        record._check_status()
                except Exception as e:
                    _logger.error("Couldn't check status of account.move with id %d: %s", record.id, str(e))

    def _check_status(self, force_write=False):
        self.ensure_one()
        if self.state == 'draft':
            params = {
                'document_token': self.extract_remote_id,
                'account_token': self._get_iap_account().account_token,
            }
            result = self._contact_iap_extract('/api/extract/invoice/2/get_result', params=params)
            self.extract_status_code = result['status_code']
            if result['status_code'] == SUCCESS:
                self.extract_state = "waiting_validation"
                ocr_results = result['results'][0]
                if 'full_text_annotation' in ocr_results:
                    self.message_main_attachment_id.index_content = ocr_results['full_text_annotation']

                if ocr_results.get('type') == 'refund' and self.move_type in ('in_invoice', 'out_invoice'):
                    # We only switch from an invoice to a credit note, not the other way around.
                    # We assume that if the user has specifically created a credit note, it is indeed a credit note.
                    self.action_switch_invoice_into_refund_credit_note()

                self._save_form(ocr_results, force_write=force_write)

                if not self.extract_word_ids:  # We don't want to recreate the boxes when the user clicks on "Reload AI data"
                    fields_with_boxes = ['supplier', 'date', 'due_date', 'invoice_id', 'currency', 'VAT_Number', 'total']
                    for field in fields_with_boxes:
                        if field in ocr_results:
                            value = ocr_results[field]
                            data = []

                            # We need to make sure that only one word is selected.
                            # Once this flag is set, the next words can't be set as selected.
                            ocr_chosen_found = False
                            for word in value["words"]:
                                ocr_chosen = value["selected_value"] == word and not ocr_chosen_found
                                if ocr_chosen:
                                    ocr_chosen_found = True
                                data.append((0, 0, {
                                    "field": field,
                                    "ocr_selected": ocr_chosen,
                                    "user_selected": ocr_chosen,
                                    "word_text": word['content'],
                                    "word_page": word['page'],
                                    "word_box_midX": word['coords'][0],
                                    "word_box_midY": word['coords'][1],
                                    "word_box_width": word['coords'][2],
                                    "word_box_height": word['coords'][3],
                                    "word_box_angle": word['coords'][4],
                                }))
                            self.write({'extract_word_ids': data})
            elif result['status_code'] == NOT_READY:
                self.extract_state = 'extract_not_ready'
            else:
                self.extract_state = 'error_status'

    def _save_form(self, ocr_results, force_write=False):
        date_ocr = ocr_results['date']['selected_value']['content'] if 'date' in ocr_results else ""
        due_date_ocr = ocr_results['due_date']['selected_value']['content'] if 'due_date' in ocr_results else ""
        total_ocr = ocr_results['total']['selected_value']['content'] if 'total' in ocr_results else 0.0
        invoice_id_ocr = ocr_results['invoice_id']['selected_value']['content'] if 'invoice_id' in ocr_results else ""
        currency_ocr = ocr_results['currency']['selected_value']['content'] if 'currency' in ocr_results else ""
        payment_ref_ocr = ocr_results['payment_ref']['selected_value']['content'] if 'payment_ref' in ocr_results else ""
        iban_ocr = ocr_results['iban']['selected_value']['content'] if 'iban' in ocr_results else ""
        SWIFT_code_ocr = json.loads(ocr_results['SWIFT_code']['selected_value']['content']) if 'SWIFT_code' in ocr_results else None
        qr_bill_ocr = ocr_results['qr-bill']['selected_value']['content'] if 'qr-bill' in ocr_results else None
        total_tax_amount = ocr_results['global_taxes_amount']['selected_value']['content'] if 'global_taxes_amount' in ocr_results else 0.0

        with self._get_edi_creation() as move_form:
            if not move_form.partner_id:
                partner_id, created = self._get_partner(ocr_results)
                if partner_id:
                    move_form.partner_id = partner_id
                    if created and iban_ocr and not move_form.partner_bank_id and self.is_purchase_document():
                        bank_account = self.env['res.partner.bank'].search([('acc_number', '=ilike', iban_ocr), *self._domain_company()])
                        if bank_account:
                            if bank_account.partner_id == move_form.partner_id.id:
                                move_form.partner_bank_id = bank_account
                        else:
                            vals = {
                                'partner_id': move_form.partner_id.id,
                                'acc_number': iban_ocr
                            }
                            if SWIFT_code_ocr:
                                bank_id = self.env['res.bank'].search([('bic', '=', SWIFT_code_ocr['bic'])], limit=1)
                                if bank_id:
                                    vals['bank_id'] = bank_id.id
                                if not bank_id and SWIFT_code_ocr['verified_bic']:
                                    country_id = self.env['res.country'].search([('code', '=', SWIFT_code_ocr['country_code'])], limit=1)
                                    if country_id:
                                        vals['bank_id'] = self.env['res.bank'].create({'name': SWIFT_code_ocr['name'], 'country': country_id.id, 'city': SWIFT_code_ocr['city'], 'bic': SWIFT_code_ocr['bic']}).id
                            move_form.partner_bank_id = self.with_context(clean_context(self.env.context)).env['res.partner.bank'].create(vals)

            if qr_bill_ocr:
                qr_content_list = qr_bill_ocr.splitlines()
                # Supplier and client sections have an offset of 16
                index_offset = 16 if self.is_sale_document() else 0
                if not move_form.partner_id:
                    partner_name = qr_content_list[5 + index_offset]
                    move_form.partner_id = self.env["res.partner"].with_context(clean_context(self.env.context)).create({
                        'name': partner_name,
                        'is_company': True,
                    })

                partner = move_form.partner_id
                address_type = qr_content_list[4 + index_offset]
                if address_type == 'S':
                    if not partner.street:
                        street = qr_content_list[6 + index_offset]
                        house_nb = qr_content_list[7 + index_offset]
                        partner.street = " ".join((street, house_nb))

                    if not partner.zip:
                        partner.zip = qr_content_list[8 + index_offset]

                    if not partner.city:
                        partner.city = qr_content_list[9 + index_offset]

                elif address_type == 'K':
                    if not partner.street:
                        partner.street = qr_content_list[6 + index_offset]
                        partner.street2 = qr_content_list[7 + index_offset]

                country_code = qr_content_list[10 + index_offset]
                if not partner.country_id and country_code:
                    country = self.env['res.country'].search([('code', '=', country_code)])
                    partner.country_id = country and country.id

                if self.is_purchase_document():
                    iban = qr_content_list[3]
                    if iban and not self.env['res.partner.bank'].search([('acc_number', '=ilike', iban)]):
                        move_form.partner_bank_id = self.with_context(clean_context(self.env.context)).env['res.partner.bank'].create({
                            'acc_number': iban,
                            'company_id': move_form.company_id.id,
                            'currency_id': move_form.currency_id.id,
                            'partner_id': partner.id,
                        })

            due_date_move_form = move_form.invoice_date_due  # remember the due_date, as it could be modified by the onchange() of invoice_date
            context_create_date = fields.Date.context_today(self, self.create_date)
            if date_ocr and (not move_form.invoice_date or move_form.invoice_date == context_create_date):
                move_form.invoice_date = date_ocr
            if due_date_ocr and due_date_move_form == context_create_date:
                if date_ocr == due_date_ocr and move_form.partner_id and move_form.partner_id.property_supplier_payment_term_id:
                    # if the invoice date and the due date found by the OCR are the same, we use the payment terms of the detected supplier instead, if there is one
                    move_form.invoice_payment_term_id = move_form.partner_id.property_supplier_payment_term_id
                else:
                    move_form.invoice_date_due = due_date_ocr

            if self.is_purchase_document() and not move_form.ref:
                move_form.ref = invoice_id_ocr

            if self.is_sale_document() and self.quick_edit_mode:
                move_form.name = invoice_id_ocr

            if payment_ref_ocr and not move_form.payment_reference:
                move_form.payment_reference = payment_ref_ocr

            add_lines = not move_form.invoice_line_ids
            if add_lines:
                if currency_ocr and move_form.currency_id == move_form.company_currency_id:
                    currency = self._get_currency(currency_ocr, move_form.partner_id)
                    if currency:
                        move_form.currency_id = currency

                vals_invoice_lines = self._get_invoice_lines(ocr_results)
                # Create the lines with only the name for account_predictive_bills
                move_form.invoice_line_ids = [
                    Command.create({'name': line_vals.pop('name')})
                    for line_vals in vals_invoice_lines
                ]

        if add_lines:
            # We needed to close the first _get_edi_creation context to let account_predictive_bills do the predictions based on the label
            with self._get_edi_creation() as move_form:
                # Now edit them with the correct amount and apply the taxes
                for line, ocr_line_vals in zip(move_form.invoice_line_ids[-len(vals_invoice_lines):], vals_invoice_lines):
                    line.write({
                        'price_unit': ocr_line_vals['price_unit'],
                        'quantity': ocr_line_vals['quantity'],
                    })
                    is_positive_tax_found = False
                    taxes_dict = {}
                    for tax in line.tax_ids:
                        taxes_dict[(tax.amount, tax.amount_type, tax.price_include)] = {
                            'found_by_OCR': False,
                            'tax_record': tax,
                        }
                    for taxes_record in ocr_line_vals['tax_ids']:
                        tax_tuple = (taxes_record.amount, taxes_record.amount_type, taxes_record.price_include)
                        if taxes_record.amount > 0.0:
                            is_positive_tax_found = True
                        if tax_tuple not in taxes_dict:
                            line.tax_ids = [Command.link(taxes_record.id)]
                        else:
                            taxes_dict[tax_tuple]['found_by_OCR'] = True
                        if taxes_record.price_include:
                            line.price_unit *= 1 + taxes_record.amount / 100
                    for tax_info in taxes_dict.values():
                        if not tax_info['found_by_OCR']:
                            amount_before = line.price_total
                            line.tax_ids = [Command.unlink(tax_info['tax_record'].id)]
                            # If the total amount didn't change after removing it, we can actually leave it.
                            # This is intended as a way to keep intra-community taxes
                            if line.price_total == amount_before and not is_positive_tax_found:
                                line.tax_ids = [Command.link(tax_info['tax_record'].id)]

            # Check the tax roundings after the tax lines have been synced
            tax_amount_rounding_error = total_ocr - self.tax_totals['amount_total']
            threshold = len(vals_invoice_lines) * move_form.currency_id.rounding
            # Check if tax amounts detected by the ocr are correct and
            # replace the taxes that caused the rounding error in case of indian localization
            if not move_form.currency_id.is_zero(tax_amount_rounding_error) and self.is_indian_taxes():
                fixed_rounding_error = total_ocr - total_tax_amount - self.tax_totals['amount_untaxed']
                tax_totals = self.tax_totals
                tax_groups = tax_totals['groups_by_subtotal']['Untaxed Amount']
                if move_form.currency_id.is_zero(fixed_rounding_error) and tax_groups:
                    tax = total_tax_amount / len(tax_groups)
                    for tax_total in tax_groups:
                        tax_total.update({
                            'tax_group_amount': tax,
                            'formatted_tax_group_amount': formatLang(self.env, tax, currency_obj=self.currency_id),
                        })
                    self.tax_totals = tax_totals

            if (
                not move_form.currency_id.is_zero(tax_amount_rounding_error) and
                float_compare(abs(tax_amount_rounding_error), threshold, precision_digits=2) <= 0
            ):
                self._check_total_amount(total_ocr)

    def buy_credits(self):
        url = self.env['iap.account'].get_credits_url(base_url='', service_name='invoice_ocr')
        return {
            'type': 'ir.actions.act_url',
            'url': url,
        }
