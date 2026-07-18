CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    active INTEGER NOT NULL
);

CREATE VIEW active_customers AS
SELECT id FROM customers WHERE active = 1;

CREATE PROCEDURE refresh_orders AS
BEGIN
    INSERT INTO order_archive SELECT * FROM orders;
    UPDATE customers SET active = 1 WHERE id IN (SELECT customer_id FROM orders);
    DELETE FROM sessions WHERE expired = 1;
END;
