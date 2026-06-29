-- 0069: newsletter_subscribers — newsletter signup capture from the website.
-- Powers the NewsletterModal on the Omerion marketing site. Distinct from
-- newsletter_materials (outbound content the agency sends); this stores the
-- people who subscribe. Mirrors the waitlist_entries pattern (0068).
CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email      TEXT UNIQUE NOT NULL,
    name       TEXT,
    industry   TEXT,
    role       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_newsletter_subscribers_email ON newsletter_subscribers (email);
