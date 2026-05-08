/*
 * Simplified C port of rag-test/test.py
 * Features:
 * 1) Lexer for a tiny language (def/if/else/while, assignment, expressions)
 * 2) LL(1)-style parser using recursive descent
 * 3) Test harness with valid/invalid samples
 *
 * Build (MinGW/GCC):
 *   gcc -std=c11 -O2 -Wall -Wextra -pedantic rag-test/test.c -o rag-test/test.exe
 */

#include <ctype.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_TOKEN_TEXT 64
#define MAX_TOKENS 4096
#define MAX_ERROR_MSG 256

typedef enum {
    TOK_DEF,
    TOK_IF,
    TOK_ELSE,
    TOK_WHILE,
    TOK_ID,
    TOK_NUM,
    TOK_PLUS,
    TOK_MINUS,
    TOK_STAR,
    TOK_SLASH,
    TOK_ASSIGN,
    TOK_EQ,
    TOK_NEQ,
    TOK_LT,
    TOK_GT,
    TOK_LPAREN,
    TOK_RPAREN,
    TOK_LBRACE,
    TOK_RBRACE,
    TOK_SEMI,
    TOK_COMMA,
    TOK_EOF,
    TOK_INVALID
} TokenType;

typedef struct {
    TokenType type;
    char text[MAX_TOKEN_TEXT];
    int pos;
} Token;

typedef struct {
    Token data[MAX_TOKENS];
    int size;
} TokenList;

typedef struct {
    bool ok;
    int error_pos;
    char error_msg[MAX_ERROR_MSG];
} ParseResult;

typedef struct {
    const TokenList *tokens;
    int pos;
    ParseResult result;
} Parser;

static const char *token_name(TokenType t) {
    switch (t) {
        case TOK_DEF: return "DEF";
        case TOK_IF: return "IF";
        case TOK_ELSE: return "ELSE";
        case TOK_WHILE: return "WHILE";
        case TOK_ID: return "ID";
        case TOK_NUM: return "NUM";
        case TOK_PLUS: return "PLUS";
        case TOK_MINUS: return "MINUS";
        case TOK_STAR: return "STAR";
        case TOK_SLASH: return "SLASH";
        case TOK_ASSIGN: return "ASSIGN";
        case TOK_EQ: return "EQ";
        case TOK_NEQ: return "NEQ";
        case TOK_LT: return "LT";
        case TOK_GT: return "GT";
        case TOK_LPAREN: return "LPAREN";
        case TOK_RPAREN: return "RPAREN";
        case TOK_LBRACE: return "LBRACE";
        case TOK_RBRACE: return "RBRACE";
        case TOK_SEMI: return "SEMI";
        case TOK_COMMA: return "COMMA";
        case TOK_EOF: return "EOF";
        default: return "INVALID";
    }
}

static bool push_token(TokenList *out, TokenType type, const char *text, int pos) {
    if (out->size >= MAX_TOKENS) {
        return false;
    }
    Token *t = &out->data[out->size++];
    t->type = type;
    t->pos = pos;
    snprintf(t->text, sizeof(t->text), "%s", text);
    return true;
}

static TokenType keyword_or_id(const char *word) {
    if (strcmp(word, "def") == 0) return TOK_DEF;
    if (strcmp(word, "if") == 0) return TOK_IF;
    if (strcmp(word, "else") == 0) return TOK_ELSE;
    if (strcmp(word, "while") == 0) return TOK_WHILE;
    return TOK_ID;
}

static bool lex(const char *src, TokenList *out, char *err, size_t err_cap) {
    int i = 0;
    out->size = 0;

    while (src[i] != '\0') {
        char ch = src[i];

        if (isspace((unsigned char)ch)) {
            i++;
            continue;
        }

        if (ch == '/' && src[i + 1] == '/') {
            i += 2;
            while (src[i] != '\0' && src[i] != '\n') {
                i++;
            }
            continue;
        }

        if (ch == '=' && src[i + 1] == '=') {
            if (!push_token(out, TOK_EQ, "==", i)) goto token_overflow;
            i += 2;
            continue;
        }
        if (ch == '!' && src[i + 1] == '=') {
            if (!push_token(out, TOK_NEQ, "!=", i)) goto token_overflow;
            i += 2;
            continue;
        }

        switch (ch) {
            case '+': if (!push_token(out, TOK_PLUS, "+", i)) goto token_overflow; i++; continue;
            case '-': if (!push_token(out, TOK_MINUS, "-", i)) goto token_overflow; i++; continue;
            case '*': if (!push_token(out, TOK_STAR, "*", i)) goto token_overflow; i++; continue;
            case '/': if (!push_token(out, TOK_SLASH, "/", i)) goto token_overflow; i++; continue;
            case '=': if (!push_token(out, TOK_ASSIGN, "=", i)) goto token_overflow; i++; continue;
            case '<': if (!push_token(out, TOK_LT, "<", i)) goto token_overflow; i++; continue;
            case '>': if (!push_token(out, TOK_GT, ">", i)) goto token_overflow; i++; continue;
            case '(': if (!push_token(out, TOK_LPAREN, "(", i)) goto token_overflow; i++; continue;
            case ')': if (!push_token(out, TOK_RPAREN, ")", i)) goto token_overflow; i++; continue;
            case '{': if (!push_token(out, TOK_LBRACE, "{", i)) goto token_overflow; i++; continue;
            case '}': if (!push_token(out, TOK_RBRACE, "}", i)) goto token_overflow; i++; continue;
            case ';': if (!push_token(out, TOK_SEMI, ";", i)) goto token_overflow; i++; continue;
            case ',': if (!push_token(out, TOK_COMMA, ",", i)) goto token_overflow; i++; continue;
            default: break;
        }

        if (isdigit((unsigned char)ch)) {
            int start = i;
            while (isdigit((unsigned char)src[i])) i++;
            int len = i - start;
            char buf[MAX_TOKEN_TEXT];
            if (len >= (int)sizeof(buf)) len = (int)sizeof(buf) - 1;
            memcpy(buf, src + start, (size_t)len);
            buf[len] = '\0';
            if (!push_token(out, TOK_NUM, buf, start)) goto token_overflow;
            continue;
        }

        if (isalpha((unsigned char)ch) || ch == '_') {
            int start = i;
            while (isalnum((unsigned char)src[i]) || src[i] == '_') i++;
            int len = i - start;
            char buf[MAX_TOKEN_TEXT];
            if (len >= (int)sizeof(buf)) len = (int)sizeof(buf) - 1;
            memcpy(buf, src + start, (size_t)len);
            buf[len] = '\0';
            if (!push_token(out, keyword_or_id(buf), buf, start)) goto token_overflow;
            continue;
        }

        snprintf(err, err_cap, "Lexer error at pos %d: illegal character '%c'", i, ch);
        return false;
    }

    if (!push_token(out, TOK_EOF, "$", i)) goto token_overflow;
    return true;

token_overflow:
    snprintf(err, err_cap, "Lexer error: token list overflow");
    return false;
}

static const Token *cur(Parser *p) {
    if (p->pos < p->tokens->size) {
        return &p->tokens->data[p->pos];
    }
    return &p->tokens->data[p->tokens->size - 1];
}

static void fail(Parser *p, const char *msg) {
    if (!p->result.ok) return;
    p->result.ok = false;
    p->result.error_pos = cur(p)->pos;
    snprintf(p->result.error_msg, sizeof(p->result.error_msg), "%s", msg);
}

static bool match(Parser *p, TokenType t) {
    if (!p->result.ok) return false;
    const Token *tk = cur(p);
    if (tk->type == t) {
        p->pos++;
        return true;
    }
    char msg[MAX_ERROR_MSG];
    snprintf(msg, sizeof(msg), "Expected %s, got %s ('%s') at pos %d",
             token_name(t), token_name(tk->type), tk->text, tk->pos);
    fail(p, msg);
    return false;
}

/* Grammar (LL style):
 * Program    -> StmtList
 * StmtList   -> Stmt StmtList | epsilon
 * Stmt       -> FuncDef | AssignStmt | IfStmt | WhileStmt
 * FuncDef    -> def id ( Params ) { StmtList }
 * Params     -> id ParamsTail | epsilon
 * ParamsTail -> , id ParamsTail | epsilon
 * AssignStmt -> id = Expr ;
 * IfStmt     -> if ( Expr ) { StmtList } ElsePart
 * ElsePart   -> else { StmtList } | epsilon
 * WhileStmt  -> while ( Expr ) { StmtList }
 * Expr       -> Term ExprTail
 * ExprTail   -> (+|-|<|>|==|!=) Term ExprTail | epsilon
 * Term       -> Factor TermTail
 * TermTail   -> (*|/) Factor TermTail | epsilon
 * Factor     -> ( Expr ) | id | num
 */

static void parse_program(Parser *p);
static void parse_stmt_list(Parser *p);
static void parse_stmt(Parser *p);
static void parse_func_def(Parser *p);
static void parse_params(Parser *p);
static void parse_params_tail(Parser *p);
static void parse_assign_stmt(Parser *p);
static void parse_if_stmt(Parser *p);
static void parse_else_part(Parser *p);
static void parse_while_stmt(Parser *p);
static void parse_expr(Parser *p);
static void parse_expr_tail(Parser *p);
static void parse_term(Parser *p);
static void parse_term_tail(Parser *p);
static void parse_factor(Parser *p);

static bool is_stmt_first(TokenType t) {
    return t == TOK_DEF || t == TOK_ID || t == TOK_IF || t == TOK_WHILE;
}

static void parse_program(Parser *p) {
    parse_stmt_list(p);
}

static void parse_stmt_list(Parser *p) {
    while (p->result.ok) {
        TokenType t = cur(p)->type;
        if (is_stmt_first(t)) {
            parse_stmt(p);
            continue;
        }
        if (t == TOK_RBRACE || t == TOK_EOF) {
            return;
        }
        char msg[MAX_ERROR_MSG];
        snprintf(msg, sizeof(msg), "Invalid token '%s' in StmtList at pos %d",
                 cur(p)->text, cur(p)->pos);
        fail(p, msg);
        return;
    }
}

static void parse_stmt(Parser *p) {
    TokenType t = cur(p)->type;
    if (t == TOK_DEF) {
        parse_func_def(p);
    } else if (t == TOK_ID) {
        parse_assign_stmt(p);
    } else if (t == TOK_IF) {
        parse_if_stmt(p);
    } else if (t == TOK_WHILE) {
        parse_while_stmt(p);
    } else {
        char msg[MAX_ERROR_MSG];
        snprintf(msg, sizeof(msg), "Invalid statement start '%s' at pos %d",
                 cur(p)->text, cur(p)->pos);
        fail(p, msg);
    }
}

static void parse_func_def(Parser *p) {
    match(p, TOK_DEF);
    match(p, TOK_ID);
    match(p, TOK_LPAREN);
    parse_params(p);
    match(p, TOK_RPAREN);
    match(p, TOK_LBRACE);
    parse_stmt_list(p);
    match(p, TOK_RBRACE);
}

static void parse_params(Parser *p) {
    if (cur(p)->type == TOK_ID) {
        match(p, TOK_ID);
        parse_params_tail(p);
    }
}

static void parse_params_tail(Parser *p) {
    while (cur(p)->type == TOK_COMMA) {
        match(p, TOK_COMMA);
        match(p, TOK_ID);
    }
}

static void parse_assign_stmt(Parser *p) {
    match(p, TOK_ID);
    match(p, TOK_ASSIGN);
    parse_expr(p);
    match(p, TOK_SEMI);
}

static void parse_if_stmt(Parser *p) {
    match(p, TOK_IF);
    match(p, TOK_LPAREN);
    parse_expr(p);
    match(p, TOK_RPAREN);
    match(p, TOK_LBRACE);
    parse_stmt_list(p);
    match(p, TOK_RBRACE);
    parse_else_part(p);
}

static void parse_else_part(Parser *p) {
    if (cur(p)->type == TOK_ELSE) {
        match(p, TOK_ELSE);
        match(p, TOK_LBRACE);
        parse_stmt_list(p);
        match(p, TOK_RBRACE);
    }
}

static void parse_while_stmt(Parser *p) {
    match(p, TOK_WHILE);
    match(p, TOK_LPAREN);
    parse_expr(p);
    match(p, TOK_RPAREN);
    match(p, TOK_LBRACE);
    parse_stmt_list(p);
    match(p, TOK_RBRACE);
}

static bool is_expr_op(TokenType t) {
    return t == TOK_PLUS || t == TOK_MINUS || t == TOK_LT || t == TOK_GT || t == TOK_EQ || t == TOK_NEQ;
}

static bool is_term_op(TokenType t) {
    return t == TOK_STAR || t == TOK_SLASH;
}

static void parse_expr(Parser *p) {
    parse_term(p);
    parse_expr_tail(p);
}

static void parse_expr_tail(Parser *p) {
    while (is_expr_op(cur(p)->type)) {
        p->pos++; /* consume operator */
        parse_term(p);
    }
}

static void parse_term(Parser *p) {
    parse_factor(p);
    parse_term_tail(p);
}

static void parse_term_tail(Parser *p) {
    while (is_term_op(cur(p)->type)) {
        p->pos++; /* consume operator */
        parse_factor(p);
    }
}

static void parse_factor(Parser *p) {
    TokenType t = cur(p)->type;
    if (t == TOK_LPAREN) {
        match(p, TOK_LPAREN);
        parse_expr(p);
        match(p, TOK_RPAREN);
    } else if (t == TOK_ID) {
        match(p, TOK_ID);
    } else if (t == TOK_NUM) {
        match(p, TOK_NUM);
    } else {
        char msg[MAX_ERROR_MSG];
        snprintf(msg, sizeof(msg), "Invalid factor '%s' at pos %d", cur(p)->text, cur(p)->pos);
        fail(p, msg);
    }
}

static ParseResult parse_ll1(const TokenList *tokens) {
    Parser p;
    p.tokens = tokens;
    p.pos = 0;
    p.result.ok = true;
    p.result.error_pos = 0;
    p.result.error_msg[0] = '\0';

    parse_program(&p);

    if (p.result.ok && cur(&p)->type != TOK_EOF) {
        char msg[MAX_ERROR_MSG];
        snprintf(msg, sizeof(msg), "Unexpected trailing token '%s' at pos %d", cur(&p)->text, cur(&p)->pos);
        fail(&p, msg);
    }

    return p.result;
}

static void print_tokens(const TokenList *tokens) {
    printf("Tokens:\n");
    for (int i = 0; i < tokens->size; ++i) {
        const Token *t = &tokens->data[i];
        printf("  [%3d] %-8s '%s' (pos=%d)\n", i, token_name(t->type), t->text, t->pos);
    }
}

static void run_test_case(const char *name, const char *code, bool expect_ok) {
    char err[256] = {0};
    TokenList tokens;

    printf("\n============================================================\n");
    printf("Test: %s\n", name);
    printf("Code: %s\n", code);
    printf("============================================================\n");

    if (!lex(code, &tokens, err, sizeof(err))) {
        if (!expect_ok) {
            printf("PASS (expected failure): %s\n", err);
        } else {
            printf("FAIL: %s\n", err);
        }
        return;
    }

    print_tokens(&tokens);
    ParseResult r = parse_ll1(&tokens);

    if (r.ok && expect_ok) {
        printf("PASS: parse success\n");
    } else if (!r.ok && !expect_ok) {
        printf("PASS (expected failure): %s\n", r.error_msg);
    } else if (r.ok && !expect_ok) {
        printf("FAIL: expected failure but parse succeeded\n");
    } else {
        printf("FAIL: %s\n", r.error_msg);
    }
}

int main(void) {
    const char *test_code_1 =
        "def foo(a, b) { "
        "x = a + b * 2 ; "
        "if (x > 10) { "
        "x = x - 1 ; "
        "} else { "
        "x = 0 ; "
        "} "
        "while (x < 100) { "
        "x = x + 1 ; "
        "} "
        "}";

    const char *test_code_2 =
        "def compute(n) { "
        "result = 0 ; "
        "i = 1 ; "
        "while (i < n) { "
        "if (i > 5) { "
        "result = result + i * 2 ; "
        "} else { "
        "result = result + i ; "
        "} "
        "i = i + 1 ; "
        "} "
        "} "
        "x = 42 ; "
        "if (x == 42) { "
        "y = x + 1 ; "
        "}";

    run_test_case("Comprehensive sample", test_code_1, true);
    run_test_case("Nested sample", test_code_2, true);

    run_test_case("Missing semicolon", "x = 1", false);
    run_test_case("Extra right paren", "x = (1 + 2)) ;", false);
    run_test_case("Missing right brace", "if (x > 0) { y = 1 ;", false);
    run_test_case("Illegal identifier start", "123abc = 1 ;", false);
    run_test_case("Empty input (legal)", "", true);

    return 0;
}
