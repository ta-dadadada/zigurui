/* 固定版utf8procの型・フラグをZigへ漏らさない小さなC ABI。 */
#include "utf8proc.h"

ptrdiff_t zigurui_decompose(int32_t cp, int32_t *out, ptrdiff_t capacity, bool fold) {
    unsigned int options = fold ? UTF8PROC_CASEFOLD :
        (UTF8PROC_STABLE | UTF8PROC_COMPAT | UTF8PROC_DECOMPOSE);
    return utf8proc_decompose_char(cp, out, capacity, options, NULL);
}
uint8_t zigurui_combining_class(int32_t cp) {
    return (uint8_t)utf8proc_get_property(cp)->combining_class;
}
int32_t zigurui_compose_pair(int32_t first, int32_t second) {
    int32_t pair[2] = {first, second};
    return utf8proc_normalize_utf32(pair, 2, UTF8PROC_STABLE | UTF8PROC_COMPOSE) == 1 ? pair[0] : -1;
}
/* テスト用の全体変換。productionの由来追跡とは別の実装経路。 */
ptrdiff_t zigurui_reference_map(const uint8_t *input, ptrdiff_t length, uint8_t **out, bool fold) {
    unsigned int options = fold ? UTF8PROC_CASEFOLD :
        (UTF8PROC_STABLE | UTF8PROC_COMPAT | UTF8PROC_COMPOSE);
    return utf8proc_map(input, length, out, options);
}
