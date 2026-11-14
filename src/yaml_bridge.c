//! libyamlの所有権とイベント表現をZigから隔離する。
#include "yaml.h"

typedef struct {
    yaml_parser_t parser;
    yaml_event_t event;
    int has_event;
} zigurui_yaml_parser;

typedef struct {
    int kind;
    const unsigned char *value;
    size_t length;
    int plain;
} zigurui_yaml_event;

/// inputはdeleteまで借用する。失敗時はNULLで、内部の確保は残さない。
void *zigurui_yaml_new(const unsigned char *input, size_t length) {
    zigurui_yaml_parser *state = calloc(1, sizeof(*state));
    if (!state) return NULL;
    if (!yaml_parser_initialize(&state->parser)) {
        free(state);
        return NULL;
    }
    /* 単一スレッドCLIの固定値。Zig側では値ノードの深さを別途16に制限する。 */
    yaml_set_max_nest_level(32);
    yaml_parser_set_encoding(&state->parser, YAML_UTF8_ENCODING);
    yaml_parser_set_input_string(&state->parser, input, length);
    return state;
}

/// valueは次のnextまたはdeleteまで借用可能。-1構文、-2確保、-3非対応、-4深さ。
int zigurui_yaml_next(void *opaque, zigurui_yaml_event *view) {
    zigurui_yaml_parser *state = opaque;
    if (state->has_event) {
        yaml_event_delete(&state->event);
        state->has_event = 0;
    }
    memset(view, 0, sizeof(*view));
    if (!yaml_parser_parse(&state->parser, &state->event)) {
        if (state->parser.problem && strstr(state->parser.problem, "Maximum nesting level")) return -4;
        return state->parser.error == YAML_MEMORY_ERROR ? -2 : -1;
    }
    state->has_event = 1;
    switch (state->event.type) {
        case YAML_STREAM_START_EVENT: view->kind = 1; break;
        case YAML_STREAM_END_EVENT: view->kind = 2; break;
        case YAML_DOCUMENT_START_EVENT:
            if (state->event.data.document_start.version_directive ||
                state->event.data.document_start.tag_directives.start != state->event.data.document_start.tag_directives.end) return -3;
            view->kind = 3;
            break;
        case YAML_DOCUMENT_END_EVENT: view->kind = 4; break;
        case YAML_SCALAR_EVENT:
            if (state->event.data.scalar.anchor || state->event.data.scalar.tag) return -3;
            view->kind = 5;
            view->value = state->event.data.scalar.value;
            view->length = state->event.data.scalar.length;
            view->plain = state->event.data.scalar.style == YAML_PLAIN_SCALAR_STYLE;
            break;
        case YAML_MAPPING_START_EVENT:
            if (state->event.data.mapping_start.anchor || state->event.data.mapping_start.tag) return -3;
            view->kind = 6;
            break;
        case YAML_MAPPING_END_EVENT: view->kind = 7; break;
        case YAML_SEQUENCE_START_EVENT:
            if (state->event.data.sequence_start.anchor || state->event.data.sequence_start.tag) return -3;
            view->kind = 8;
            break;
        case YAML_SEQUENCE_END_EVENT: view->kind = 9; break;
        default: return -3;
    }
    return 1;
}

/// 現在のイベントとparserが所有する全メモリを解放する。
void zigurui_yaml_delete(void *opaque) {
    zigurui_yaml_parser *state = opaque;
    if (!state) return;
    if (state->has_event) yaml_event_delete(&state->event);
    yaml_parser_delete(&state->parser);
    free(state);
}
