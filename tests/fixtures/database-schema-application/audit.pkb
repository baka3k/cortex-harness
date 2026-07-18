CREATE OR REPLACE PACKAGE BODY audit_pkg AS
    FUNCTION user_roles RETURN SYS_REFCURSOR IS
        result_cursor SYS_REFCURSOR;
    BEGIN
        OPEN result_cursor FOR
            SELECT u.id, r.name
            FROM users u
            JOIN roles r ON r.id = u.role_id;
        RETURN result_cursor;
    END user_roles;

    PROCEDURE log_user IS
    BEGIN
        INSERT INTO audit_log (id, user_id)
        SELECT 1, id FROM users;
        UPDATE users SET last_seen = SYSDATE;
    END log_user;
END audit_pkg;
