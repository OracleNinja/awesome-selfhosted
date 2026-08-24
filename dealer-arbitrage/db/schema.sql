-- Dealer Arbitrage Agent — SQLite schema (Stage 11)
-- Every externally-visible action MUST be mirrored into audit_log.
-- Design notes:
--   * companies is the master entity. targets is the scored *pursuit* of a company.
--   * Nothing that asserts a fact about a company may be stored without a row in
--     sources providing the evidence URL + quoted text (Stage 13).
--   * Money is stored in integer CENTS to avoid float drift. NULL == unknown,
--     which is NOT the same as 0. Never coerce unknown to zero in cost math.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- companies
CREATE TABLE IF NOT EXISTS companies (
    id                  INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL,
    normalized_name     TEXT    NOT NULL UNIQUE,   -- lowercased/stripped, dedupe key
    website             TEXT,
    domain              TEXT,
    company_type        TEXT    NOT NULL DEFAULT 'unknown'
                        CHECK (company_type IN ('manufacturer','distributor','dealer',
                                                'wholesaler','broker','liquidator',
                                                'marketplace','unknown')),
    location            TEXT,
    city                TEXT,
    state               TEXT,
    zip                 TEXT,
    country             TEXT DEFAULT 'US',
    phone               TEXT,
    email               TEXT,
    -- Claim fields. Every non-'unknown' value REQUIRES a supporting sources row.
    dealer_status       TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (dealer_status IN ('unknown','claims_authorized_dealer',
                                                 'evidence_authorized_dealer','not_a_dealer')),
    wholesale_status    TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (wholesale_status IN ('unknown','evidence_wholesale',
                                                    'retail_only','claims_wholesale')),
    has_dealer_program  INTEGER NOT NULL DEFAULT 0 CHECK (has_dealer_program IN (0,1)),
    has_demo_program    INTEGER NOT NULL DEFAULT 0 CHECK (has_demo_program IN (0,1)),
    application_url     TEXT,
    notes               TEXT,
    identity_verified   INTEGER NOT NULL DEFAULT 0 CHECK (identity_verified IN (0,1)),
    risk_flags          TEXT,                       -- JSON array of strings
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_companies_state ON companies(state);
CREATE INDEX IF NOT EXISTS idx_companies_type  ON companies(company_type);

-- ------------------------------------------------------------------ targets
CREATE TABLE IF NOT EXISTS targets (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    discovery_query     TEXT,                       -- which Stage 2 query surfaced it
    relevance           INTEGER CHECK (relevance BETWEEN 0 AND 100),
    score               INTEGER CHECK (score BETWEEN 0 AND 100),
    score_breakdown     TEXT,                       -- JSON: signal -> {weight, evidence_id}
    scored_at           TEXT,
    -- Estimated probabilities 0..100. NULL until evidence supports an estimate.
    p_demo              INTEGER CHECK (p_demo BETWEEN 0 AND 100),
    p_free_unit         INTEGER CHECK (p_free_unit BETWEEN 0 AND 100),
    p_credited_unit     INTEGER CHECK (p_credited_unit BETWEEN 0 AND 100),
    prob_rationale      TEXT,
    strategy            TEXT,                       -- strategy key from config/strategies.json
    strategy_rank       INTEGER,
    strategy_rationale  TEXT,
    stage               TEXT NOT NULL DEFAULT 'discovered'
                        CHECK (stage IN ('discovered','researched','outreach_drafted',
                                         'awaiting_approval','contacted','responded',
                                         'negotiating','application_ready',
                                         'application_submitted','offer_received',
                                         'won','lost','disqualified','on_hold')),
    disqualified_reason TEXT,
    freight_estimate_cents INTEGER,                 -- NULL = unknown, not free
    freight_basis       TEXT,                       -- how the estimate was derived + source
    owner_next_action   TEXT,
    next_action_due     TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id)
);
CREATE INDEX IF NOT EXISTS idx_targets_stage ON targets(stage);
CREATE INDEX IF NOT EXISTS idx_targets_score ON targets(score DESC);

-- ----------------------------------------------------------------- contacts
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    full_name       TEXT,
    role            TEXT,
    email           TEXT,
    phone           TEXT,
    linkedin        TEXT,
    preferred_channel TEXT CHECK (preferred_channel IN
                        ('email','phone','web_form','linkedin','sms','in_person', NULL)),
    is_primary      INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    opted_out       INTEGER NOT NULL DEFAULT 0 CHECK (opted_out IN (0,1)),
    opted_out_at    TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);

-- ----------------------------------------------------------------- products
CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    brand               TEXT,
    model               TEXT,
    model_year          INTEGER,
    passenger_capacity  INTEGER,
    powertrain          TEXT,                       -- e.g. lithium, AGM, gas
    condition           TEXT CHECK (condition IN ('new','demo','previous_model_year',
                                                  'excess','cancelled_order','blemished',
                                                  'used','factory_sample','unknown')),
    msrp_cents          INTEGER,
    quoted_price_cents  INTEGER,
    price_source_id     INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    in_stock            INTEGER CHECK (in_stock IN (0,1)),
    availability_note   TEXT,
    source_id           INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_company ON products(company_id);
CREATE INDEX IF NOT EXISTS idx_products_model   ON products(model);

-- ------------------------------------------------------------------ sources
-- Stage 13: evidence store. Nothing gets claimed without a row here.
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    title           TEXT,
    quoted_text     TEXT NOT NULL,                  -- the exact supporting text
    claim           TEXT NOT NULL,                  -- which claim this evidences
    retrieved_at    TEXT NOT NULL DEFAULT (datetime('now')),
    retrieved_by    TEXT NOT NULL DEFAULT 'agent',
    content_hash    TEXT,
    local_copy_path TEXT,                           -- archived HTML/text snapshot
    confidence      TEXT NOT NULL DEFAULT 'medium'
                    CHECK (confidence IN ('low','medium','high')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sources_company ON sources(company_id);
CREATE INDEX IF NOT EXISTS idx_sources_claim   ON sources(claim);

-- ------------------------------------------------------------- applications
CREATE TABLE IF NOT EXISTS applications (
    id                  INTEGER PRIMARY KEY,
    target_id           INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    program_name        TEXT,                       -- "Denago dealer application" etc.
    url                 TEXT,
    method              TEXT NOT NULL DEFAULT 'web_form'
                        CHECK (method IN ('web_form','pdf','email','phone','portal')),
    field_map           TEXT,                       -- JSON: form field -> profile path
    filled_fields       TEXT,                       -- JSON: form field -> value used
    unfilled_fields     TEXT,                       -- JSON: fields needing human input
    human_required      TEXT,                       -- JSON: login/captcha/signature/etc.
    attachments         TEXT,                       -- JSON: document ids attached
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','ready_for_approval','approved',
                                          'submitted','rejected','accepted',
                                          'needs_human','cancelled')),
    readiness_report_path TEXT,
    approved_by         TEXT,
    approved_at         TEXT,
    submitted_at        TEXT,
    confirmation_ref    TEXT,
    outcome_notes       TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

-- ----------------------------------------------------------------- outreach
CREATE TABLE IF NOT EXISTS outreach (
    id              INTEGER PRIMARY KEY,
    target_id       INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    contact_id      INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    channel         TEXT NOT NULL CHECK (channel IN
                        ('email','phone','web_form','linkedin','sms','in_person')),
    direction       TEXT NOT NULL DEFAULT 'outbound'
                    CHECK (direction IN ('outbound','inbound')),
    sequence_no     INTEGER NOT NULL DEFAULT 1,     -- 1 = first touch, 2+ = follow-up
    template_key    TEXT,
    angle           TEXT,                           -- follow-ups must change the angle
    subject         TEXT,
    body            TEXT NOT NULL,
    attachments     TEXT,                           -- JSON array of document ids
    personalization TEXT,                           -- JSON: what was referenced + source ids
    claims_asserted TEXT,                           -- JSON array, each must map to evidence
    ask             TEXT,                           -- the specific request being made
    expected_outcome TEXT,
    risks           TEXT,
    cost_commitment_cents INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','pending_approval','approved','sent',
                                      'edited','cancelled','failed','bounced')),
    approval_id     INTEGER REFERENCES approvals(id) ON DELETE SET NULL,
    draft_path      TEXT,
    sent_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_outreach_target ON outreach(target_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);

-- ---------------------------------------------------------------- approvals
-- Stage 7 gate. An outreach/application may only leave 'pending_approval'
-- through a row here recorded by a human.
CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (entity_type IN
                        ('outreach','application','followup','negotiation','purchase')),
    entity_id       INTEGER NOT NULL,
    presented_at    TEXT NOT NULL DEFAULT (datetime('now')),
    presented_body  TEXT NOT NULL,                  -- exact text shown to the human
    decision        TEXT CHECK (decision IN ('APPROVE SEND','EDIT','CANCEL', NULL)),
    decided_at      TEXT,
    decided_by      TEXT,
    edit_notes      TEXT,
    cost_commitment_cents INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_approvals_entity ON approvals(entity_type, entity_id);

-- ---------------------------------------------------------------- responses
CREATE TABLE IF NOT EXISTS responses (
    id                  INTEGER PRIMARY KEY,
    target_id           INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    outreach_id         INTEGER REFERENCES outreach(id) ON DELETE SET NULL,
    contact_id          INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    channel             TEXT NOT NULL CHECK (channel IN
                            ('email','phone','web_form','linkedin','sms','in_person')),
    received_at         TEXT NOT NULL DEFAULT (datetime('now')),
    raw_body            TEXT NOT NULL,
    classification      TEXT NOT NULL CHECK (classification IN (
                            'interested','requesting_information','asking_for_dealer_credentials',
                            'requesting_business_license','offering_paid_demo','offering_free_demo',
                            'offering_credited_demo','offering_discount','requesting_initial_order',
                            'rejection','referral','unclear','suspicious')),
    classification_confidence REAL,
    classification_signals TEXT,                    -- JSON: which cues fired
    secondary_labels    TEXT,                       -- JSON array
    referral_to         TEXT,
    suggested_next_step TEXT,
    handled             INTEGER NOT NULL DEFAULT 0 CHECK (handled IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_responses_target ON responses(target_id);
CREATE INDEX IF NOT EXISTS idx_responses_class  ON responses(classification);

-- ------------------------------------------------------------------- offers
-- One row per concrete commercial proposal from a target. Landed cost is the
-- ONLY figure used for ranking (Stage 10 of core principles).
CREATE TABLE IF NOT EXISTS offers (
    id                      INTEGER PRIMARY KEY,
    target_id               INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    response_id             INTEGER REFERENCES responses(id) ON DELETE SET NULL,
    offer_type              TEXT NOT NULL CHECK (offer_type IN (
                                'free_unit','free_unit_plus_freight','credited_demo',
                                'paid_demo','discounted_purchase','consignment',
                                'previous_model_year','excess_inventory','blemished',
                                'cancelled_order','retail','other')),
    product_id              INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_description     TEXT,
    -- Landed cost components, in cents. NULL = NOT YET DISCLOSED (never assume 0).
    vehicle_price_cents     INTEGER,
    freight_cents           INTEGER,
    setup_prep_cents        INTEGER,
    destination_cents       INTEGER,
    doc_fee_cents           INTEGER,
    taxes_cents             INTEGER,
    dealer_fees_cents       INTEGER,
    required_purchase_cents INTEGER,                -- mandatory add-on / min order
    other_fees_cents        INTEGER,
    other_fees_note         TEXT,
    landed_cost_cents       INTEGER,                -- computed; NULL if any component unknown
    undisclosed_components  TEXT,                   -- JSON array of unknown component names
    credit_pct              INTEGER CHECK (credit_pct BETWEEN 0 AND 100),
    credit_conditions       TEXT,
    min_order_units         INTEGER,
    includes                TEXT,                   -- JSON: charger, warranty, parts, etc.
    expires_at              TEXT,
    status                  TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','countered','accepted_pending_approval',
                                              'accepted','declined','expired','withdrawn')),
    evidence_path           TEXT,                   -- saved copy of the offer message
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_offers_target ON offers(target_id);
CREATE INDEX IF NOT EXISTS idx_offers_landed ON offers(landed_cost_cents);

-- ------------------------------------------------------------- negotiations
CREATE TABLE IF NOT EXISTS negotiations (
    id                  INTEGER PRIMARY KEY,
    target_id           INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    offer_id            INTEGER REFERENCES offers(id) ON DELETE SET NULL,
    round_no            INTEGER NOT NULL DEFAULT 1,
    our_position        TEXT,
    their_position      TEXT,
    concession_ladder   TEXT,                       -- JSON: ranked asks still on the table
    asked_for           TEXT,                       -- JSON array of ladder items requested
    conceded            TEXT,                       -- JSON array of items we gave up
    market_citations    TEXT,                       -- JSON: {claim, price_cents, source_id}
    target_landed_cost_cents INTEGER,
    achieved_landed_cost_cents INTEGER,
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','awaiting_them','awaiting_us',
                                          'stalled','closed_won','closed_lost')),
    next_move           TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_negotiations_target ON negotiations(target_id);

-- ---------------------------------------------------------------- followups
CREATE TABLE IF NOT EXISTS followups (
    id              INTEGER PRIMARY KEY,
    target_id       INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    outreach_id     INTEGER REFERENCES outreach(id) ON DELETE CASCADE,
    day_offset      INTEGER NOT NULL,               -- 3 / 7 / 14 / 30
    due_at          TEXT NOT NULL,
    angle           TEXT NOT NULL,                  -- must differ from prior angles
    template_key    TEXT,
    draft_outreach_id INTEGER REFERENCES outreach(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled','drafted','pending_approval',
                                      'sent','skipped','cancelled','superseded')),
    skipped_reason  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_followups_due ON followups(due_at, status);

-- ---------------------------------------------------------------- documents
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN (
                        'business_license','sales_tax_permit','ein_letter','insurance',
                        'photo_storefront','photo_display_area','photo_service_area',
                        'resale_certificate','w9','bank_reference','logo','other')),
    label           TEXT NOT NULL,
    path            TEXT NOT NULL,
    sha256          TEXT,
    approved_for_sharing INTEGER NOT NULL DEFAULT 0
                    CHECK (approved_for_sharing IN (0,1)),
    approved_by     TEXT,
    approved_at     TEXT,
    contains_sensitive INTEGER NOT NULL DEFAULT 0
                    CHECK (contains_sensitive IN (0,1)),
    expires_at      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------- screenshots
CREATE TABLE IF NOT EXISTS screenshots (
    id              INTEGER PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (entity_type IN
                        ('application','outreach','response','offer','source','target')),
    entity_id       INTEGER NOT NULL,
    path            TEXT NOT NULL,
    url             TEXT,
    phase           TEXT NOT NULL DEFAULT 'pre_submission'
                    CHECK (phase IN ('pre_submission','post_submission','confirmation',
                                     'evidence','error')),
    sha256          TEXT,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_screenshots_entity ON screenshots(entity_type, entity_id);

-- -------------------------------------------------------------------- tasks
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    detail          TEXT,
    target_id       INTEGER REFERENCES targets(id) ON DELETE CASCADE,
    entity_type     TEXT,
    entity_id       INTEGER,
    kind            TEXT NOT NULL DEFAULT 'agent'
                    CHECK (kind IN ('agent','human','approval','blocked')),
    blocking_reason TEXT,                           -- login/captcha/payment/identity/etc.
    priority        INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    due_at          TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','in_progress','done','cancelled')),
    completed_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority);

-- ---------------------------------------------------------------- audit_log
-- Append-only. Every external action and every state change lands here.
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    actor           TEXT NOT NULL DEFAULT 'agent'   -- 'agent' | 'human' | 'system'
                    CHECK (actor IN ('agent','human','system')),
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       INTEGER,
    external        INTEGER NOT NULL DEFAULT 0 CHECK (external IN (0,1)),
    summary         TEXT NOT NULL,
    detail          TEXT,                           -- JSON
    approval_id     INTEGER REFERENCES approvals(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_external ON audit_log(external, ts DESC);

-- audit_log is append-only: block UPDATE and DELETE at the DB layer.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- Sending outreach requires a recorded APPROVE SEND decision.
CREATE TRIGGER IF NOT EXISTS outreach_requires_approval
BEFORE UPDATE OF status ON outreach
WHEN NEW.status = 'sent' AND (
        NEW.approval_id IS NULL
     OR NOT EXISTS (SELECT 1 FROM approvals a
                    WHERE a.id = NEW.approval_id
                      AND a.decision = 'APPROVE SEND'
                      AND a.entity_type = 'outreach'
                      AND a.entity_id = NEW.id))
BEGIN
    SELECT RAISE(ABORT,
        'outreach cannot be marked sent without an APPROVE SEND approval row');
END;

-- Submitting an application requires an explicit human approval.
CREATE TRIGGER IF NOT EXISTS application_requires_approval
BEFORE UPDATE OF status ON applications
WHEN NEW.status = 'submitted' AND (NEW.approved_by IS NULL OR NEW.approved_at IS NULL)
BEGIN
    SELECT RAISE(ABORT,
        'application cannot be marked submitted without approved_by/approved_at');
END;

-- updated_at maintenance
CREATE TRIGGER IF NOT EXISTS companies_touch AFTER UPDATE ON companies
BEGIN UPDATE companies SET updated_at = datetime('now') WHERE id = NEW.id; END;
CREATE TRIGGER IF NOT EXISTS targets_touch AFTER UPDATE ON targets
BEGIN UPDATE targets SET updated_at = datetime('now') WHERE id = NEW.id; END;
CREATE TRIGGER IF NOT EXISTS offers_touch AFTER UPDATE ON offers
BEGIN UPDATE offers SET updated_at = datetime('now') WHERE id = NEW.id; END;

-- ------------------------------------------------------------------- views
-- Stage 12: ZERO-DOLLAR OPPORTUNITIES.
-- A prospect qualifies if it has an actual $0-landed offer, OR carries evidence
-- of a demo/dealer-development program, OR is scored likely to yield a free or
-- fully-credited unit.
DROP VIEW IF EXISTS zero_dollar_opportunities;
CREATE VIEW zero_dollar_opportunities AS
SELECT
    t.id                AS target_id,
    c.name              AS company,
    c.company_type      AS type,
    c.state             AS state,
    t.score             AS score,
    t.p_free_unit       AS p_free,
    t.p_credited_unit   AS p_credited,
    t.strategy          AS strategy,
    t.stage             AS stage,
    (SELECT MIN(o.landed_cost_cents) FROM offers o
      WHERE o.target_id = t.id AND o.status IN ('open','countered','accepted'))
                        AS best_landed_cost_cents,
    (SELECT COUNT(*) FROM offers o
      WHERE o.target_id = t.id
        AND o.offer_type IN ('free_unit','free_unit_plus_freight','credited_demo'))
                        AS zero_dollar_offers,
    c.has_demo_program  AS demo_program,
    c.has_dealer_program AS dealer_program
FROM targets t
JOIN companies c ON c.id = t.company_id
WHERE t.stage NOT IN ('lost','disqualified')
  AND (
        c.has_demo_program = 1
     OR c.has_dealer_program = 1
     OR COALESCE(t.p_free_unit, 0) >= 20
     OR COALESCE(t.p_credited_unit, 0) >= 35
     OR EXISTS (SELECT 1 FROM offers o
                 WHERE o.target_id = t.id
                   AND (o.landed_cost_cents = 0
                        OR o.offer_type IN ('free_unit','free_unit_plus_freight',
                                            'credited_demo')))
  );

-- Best current offer across the whole campaign, ranked by landed cost.
-- Offers with undisclosed components sort last: an unknown is not a zero.
DROP VIEW IF EXISTS best_offers;
CREATE VIEW best_offers AS
SELECT
    o.id, o.target_id, c.name AS company, o.offer_type, o.product_description,
    o.landed_cost_cents, o.credit_pct, o.min_order_units, o.status,
    o.undisclosed_components, o.expires_at
FROM offers o
JOIN targets t   ON t.id = o.target_id
JOIN companies c ON c.id = t.company_id
WHERE o.status IN ('open','countered','accepted_pending_approval','accepted')
ORDER BY
    CASE WHEN o.landed_cost_cents IS NULL THEN 1 ELSE 0 END,
    o.landed_cost_cents ASC,
    o.credit_pct DESC;

-- Anything that requires a human before the campaign can advance.
DROP VIEW IF EXISTS human_queue;
CREATE VIEW human_queue AS
-- Approval tasks are deliberately excluded: the outreach/application rows below
-- already represent them, and listing both shows every approval twice.
SELECT 'task' AS kind, id AS entity_id, title AS label,
       COALESCE(blocking_reason, 'needs your input') AS reason, priority, due_at
  FROM tasks WHERE status IN ('open','in_progress') AND kind IN ('human','blocked')
UNION ALL
SELECT 'outreach', o.id, 'Outreach to ' || c.name, 'awaiting APPROVE SEND', 1, NULL
  FROM outreach o JOIN targets t ON t.id = o.target_id
  JOIN companies c ON c.id = t.company_id
 WHERE o.status = 'pending_approval'
UNION ALL
SELECT 'application', a.id, 'Application: ' || COALESCE(a.program_name, a.url),
       'awaiting submission approval', 1, NULL
  FROM applications a WHERE a.status IN ('ready_for_approval','needs_human');
