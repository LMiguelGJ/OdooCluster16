# -*- coding: utf-8 -*-
from odoo import fields, models, _


class AccountMove(models.Model):
    _inherit = 'account.move'

    auto_generated = fields.Boolean(string='Auto Generated Document', copy=False, default=False)
    auto_invoice_id = fields.Many2one('account.move', string='Source Invoice', readonly=True, copy=False)

    def _post(self, soft=True):
        # OVERRIDE to generate cross invoice based on company rules.
        invoices_map = {}
        posted = super()._post(soft)
        for invoice in posted.filtered(lambda move: move.is_invoice()):
            company = self.env['res.company']._find_company_from_partner(invoice.partner_id.id)
            if company and company.rule_type == 'invoice_and_refund' and not invoice.auto_generated:
                invoices_map.setdefault(company, self.env['account.move'])
                invoices_map[company] += invoice
        for company, invoices in invoices_map.items():
            context = dict(self.env.context, default_company_id=company.id)
            context.pop('default_journal_id', None)
            invoices.with_user(company.intercompany_user_id).with_context(context).with_company(company)._inter_company_create_invoices()
        return posted

    def _inter_company_create_invoices(self):
        ''' Create cross company invoices.
        :return:        The newly created invoices.
        '''

        # Prepare invoice values.
        invoices_vals_per_type = {}
        inverse_types = {
            'in_invoice': 'out_invoice',
            'in_refund': 'out_refund',
            'out_invoice': 'in_invoice',
            'out_refund': 'in_refund',
        }
        for inv in self:
            invoice_vals = inv._inter_company_prepare_invoice_data(inverse_types[inv.move_type])
            invoice_vals['invoice_line_ids'] = []
            for line in inv.invoice_line_ids:
                invoice_vals['invoice_line_ids'].append((0, 0, line._inter_company_prepare_invoice_line_data()))

            inv_new = inv.with_context(default_move_type=invoice_vals['move_type']).new(invoice_vals)
            for line in inv_new.invoice_line_ids.filtered(lambda l: l.display_type not in ('line_note', 'line_section')):
                # We need to adapt the taxes following the fiscal position, but we must keep the
                # price unit.
                price_unit = line.price_unit
                line.tax_ids = line._get_computed_taxes()
                line.price_unit = price_unit

            invoice_vals = inv_new._convert_to_write(inv_new._cache)
            invoice_vals.pop('line_ids', None)
            invoice_vals['origin_invoice'] = inv

            invoices_vals_per_type.setdefault(invoice_vals['move_type'], [])
            invoices_vals_per_type[invoice_vals['move_type']].append(invoice_vals)

        # Create invoices.
        moves = self.env['account.move']
        for invoice_type, invoices_vals in invoices_vals_per_type.items():
            for invoice in invoices_vals:
                origin_invoice = invoice['origin_invoice']
                invoice.pop('origin_invoice')
                msg = _("Automatically generated from %(origin)s of company %(company)s.", origin=origin_invoice.name, company=origin_invoice.company_id.name)
                am = self.with_context(default_type=invoice_type).create(invoice)
                am.message_post(body=msg)
                moves += am
        return moves

    def _inter_company_prepare_invoice_data(self, invoice_type):
        r''' Get values to create the invoice.
        /!\ Doesn't care about lines, see '_inter_company_prepare_invoice_line_data'.
        :return: Python dictionary of values.
        '''
        self.ensure_one()
        # We need the fiscal position in the company (already in context) we are creating the
        # invoice, not the fiscal position of the current invoice (self.company)
        delivery_partner_id = self.company_id.partner_id.address_get(['delivery'])['delivery']
        delivery_partner = self.env['res.partner'].browse(delivery_partner_id)
        fiscal_position_id = self.env['account.fiscal.position']._get_fiscal_position(
            self.company_id.partner_id, delivery=delivery_partner
        )
        return {
            'move_type': invoice_type,
            'ref': self.ref,
            'partner_id': self.company_id.partner_id.id,
            'currency_id': self.currency_id.id,
            'auto_generated': True,
            'auto_invoice_id': self.id,
            'invoice_date': self.invoice_date,
            'invoice_date_due': self.invoice_date_due,
            'payment_reference': self.payment_reference,
            'invoice_origin': _('%s Invoice: %s') % (self.company_id.name, self.name),
            'fiscal_position_id': fiscal_position_id,
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _inter_company_prepare_invoice_line_data(self):
        ''' Get values to create the invoice line.
        We prioritize the analytic distribution in the following order:
            - Default Analytic Distribution model specific to Company B
            - Analytic Distribution set for the line in Company A's document if available to Company B
        :return: Python dictionary of values.
        '''
        self.ensure_one()

        vals = {
            'display_type': self.display_type,
            'sequence': self.sequence,
            'name': self.name,
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom_id.id,
            'quantity': self.quantity,
            'discount': self.discount,
            'price_unit': self.price_unit,
        }

        company_b = self.env['res.company']._find_company_from_partner(self.move_id.partner_id.id)
        company_a_partner = self.company_id.partner_id
        company_b_default_distribution = self.env['account.analytic.distribution.model']._get_distribution({
            "product_id": self.product_id.id,
            "product_categ_id": self.product_id.categ_id.id,
            "partner_id": company_a_partner.id,
            "partner_category_id": company_a_partner.category_id.ids,
            "account_prefix": self.account_id.code,
            "company_id": company_b.id,
        })

        account_ids = [int(account_id) for account_id in self.analytic_distribution or {}]
        accounts = self.env['account.analytic.account'].browse(account_ids)
        analytic_distribution = {
            str(account_id): self.analytic_distribution[str(account_id)]
            for account_id in accounts.filtered(lambda r: not r.company_id).ids
        }

        if company_b_default_distribution or analytic_distribution:
            vals['analytic_distribution'] = dict(company_b_default_distribution, **analytic_distribution)

        return vals
