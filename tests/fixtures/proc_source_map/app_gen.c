/* Pro*C precompiler preamble — unmapped generated region */
#include <sqlca.h>
static struct sqlexd sql_ctx;

#line 5 "app.pc"
int load_customer(int customer_id) {
    char customer_name[64];
/* EXEC SQL SELECT NAME INTO :customer_name FROM CUSTOMER WHERE ID = :customer_id; */
{
    struct { int len; char *buf; } __name;
    sqlcxt((void **)0, &sql_ctx, &sqlstm, &sqlctx);
}
    log_message("loaded customer");
    return helper_validate(customer_name);
}

#line 12 "app.pc"
int helper_validate(const char *name) {
/* EXEC SQL COMMIT; */
{
    sqlcxt((void **)0, &sql_ctx, &sqlstm, &sqlctx);
}
    return 0;
}
