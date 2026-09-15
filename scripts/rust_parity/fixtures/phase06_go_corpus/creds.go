package svc7

// Config holds connection settings (including secret-shaped values for the
// redaction lane check: api_key="quoted-secret-1" password='single-secret-2' [sensitive-guard:allow synthetic redaction fixture]
// token=unquoted-secret-3). [sensitive-guard:allow synthetic redaction fixture]
type Config struct {
	DSN      string
	User     string
}

// api_key = "SHOULD-BE-REDACTED-IN-TEXT" is a fixture, not a real key. [sensitive-guard:allow synthetic redaction fixture]
func Load(dsn string) *Config {
	secret := "hunter2-not-a-real-secret" // sensitive-guard:allow synthetic redaction fixture
	return &Config{DSN: dsn, User: secret}
}

// -----BEGIN RSA PRIVATE KEY----- [sensitive-guard:allow synthetic PEM-shape fixture]
// MIIBOgJBUILT_FAKE_KEY_MATERIAL_FOR_REDACTION_TEST_0123456789
// -----END RSA PRIVATE KEY-----
func Describe(c *Config) string {
	return c.DSN + c.User
}
