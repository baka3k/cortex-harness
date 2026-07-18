CREATE TABLE audit_log (
    id NUMBER,
    user_id NUMBER
);

CREATE OR REPLACE VIEW user_roles AS
SELECT u.id, r.name
FROM users u
JOIN roles r ON r.id = u.role_id;

CREATE OR REPLACE PROCEDURE log_user AS
BEGIN
    INSERT INTO audit_log (id, user_id)
    SELECT 1, id FROM users;
    UPDATE users SET last_seen = SYSDATE;
END;
