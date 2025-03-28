# -*- coding: utf-8 -*-

from odoo import models

import re


class AccountReconcileModel(models.Model):
    _inherit = 'account.reconcile.model'

    def _get_invoice_matching_batch_payments_candidates(self, st_line, partner):
        assert self.rule_type == 'invoice_matching'
        self.env['account.batch.payment'].flush_model()

        _numerical_tokens, _exact_tokens, text_tokens = self._get_invoice_matching_st_line_tokens(st_line)
        if not text_tokens:
            return

        batch_sequence_code = 'account.inbound.batch.payment' if st_line.amount > 0.0 else 'account.outbound.batch.payment'

        sequence_prefix = self.env['ir.sequence'].sudo()\
            .search(
                [('code', '=', batch_sequence_code), ('company_id', 'in', (st_line.company_id.id, False))],
                order='company_id',
                limit=1,
            )\
            .prefix
        if not sequence_prefix:
            return

        sequence_prefix = sequence_prefix.lower()
        # sequence_prefix is very likely close to something like "BATCH/OUT/%(year)s/00002".
        # As is might change to use %(month)s or something else, we use regex to find a match.
        split_factor = re.search('%(.*)s', sequence_prefix)
        if split_factor:
            split_factor = split_factor.group(0)
        seq_pref_splits = sequence_prefix.split(split_factor)
        formatted_seq_pref_splits = [''.join(x for x in seq_split if re.match(r'[0-9a-z\s]', x)) for seq_split in seq_pref_splits]
        regex_seq_pref_rule = '[0-9]+'.join(formatted_seq_pref_splits)
        text_tokens = [x.lower() for x in text_tokens if re.search('^' + regex_seq_pref_rule, x.lower())]
        if not text_tokens:
            return

        aml_domain = self._get_invoice_matching_amls_domain(st_line, partner)
        query = self.env['account.move.line']._where_calc(aml_domain)
        tables, where_clause, where_params = query.get_sql()

        additional_conditions = []
        for token in text_tokens:
            additional_conditions.append(r"%s ~ sub.name")
            where_params.append(token)

        self._cr.execute(
            rf'''
                WITH account_batch_payment_name AS (
                    SELECT DISTINCT
                        batch.id,
                        SUBSTRING(REGEXP_REPLACE(LOWER(batch.name), '[^0-9a-z\s]', '', 'g'), '\S(?:.*\S)*') AS name,
                        ARRAY_AGG(account_move_line.id) AS aml_ids
                    FROM {tables}
                    JOIN account_payment pay ON pay.id = account_move_line.payment_id
                    JOIN account_batch_payment batch ON
                        batch.id = pay.batch_payment_id
                        AND batch.state != 'reconciled'
                    WHERE {where_clause}
                    GROUP BY batch.id, batch.name
                )
                SELECT sub.aml_ids
                FROM account_batch_payment_name sub
                WHERE {' OR '.join(additional_conditions)}
            ''',
            where_params,
        )
        candidate_ids = [aml_id for batch_amls in self._cr.fetchall() for aml_id in batch_amls[0]]
        if candidate_ids:
            return {
                'allow_auto_reconcile': True,
                'amls': self.env['account.move.line'].browse(candidate_ids),
            }

    def _get_invoice_matching_rules_map(self):
        # EXTENDS account
        res = super()._get_invoice_matching_rules_map()
        res[0].append(self._get_invoice_matching_batch_payments_candidates)
        return res
