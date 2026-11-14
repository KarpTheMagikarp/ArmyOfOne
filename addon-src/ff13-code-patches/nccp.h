// Nova Chrysalia's code patch format, read the way Nova reads it.
//
// A .nccp is Valve KeyValues text: one root object whose children with
// all-digit names are each "RVA|bytes", both hex. Nova's launcher writes the
// bytes into the game at its image base plus the RVA while the process is
// suspended at start (MemPatch.Code, GameLauncher.cs). Any other child is
// skipped, the way Nova's All(char.IsDigit) filter skips it.
//
// No Windows here, so the parse is tested on whatever machine is to hand:
//   g++ -std=c++20 -Wall -Wextra -Werror -o nccp_test nccp_test.cpp && ./nccp_test

#pragma once

#include <cctype>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace ff13_nccp {

struct Patch {
    std::uint64_t rva = 0;
    std::vector<std::uint8_t> bytes;
};

struct Parsed {
    std::vector<Patch> patches;
    std::vector<std::string> skipped;  // keys whose value didn't read as RVA|bytes
};

namespace detail {

struct Token {
    enum Kind { Text, Open, Close, End } kind = End;
    std::string text;
};

class Lexer {
public:
    explicit Lexer(std::string_view s) : s_(s) {}

    Token Next() {
        Skip();
        if (at_ >= s_.size()) return {};
        char c = s_[at_];
        if (c == '{') { ++at_; return {Token::Open, {}}; }
        if (c == '}') { ++at_; return {Token::Close, {}}; }
        if (c == '"') return Quoted();
        std::string out;
        while (at_ < s_.size() && !std::isspace(static_cast<unsigned char>(s_[at_])) &&
               s_[at_] != '{' && s_[at_] != '}' && s_[at_] != '"') {
            out.push_back(s_[at_++]);
        }
        return {Token::Text, out};
    }

private:
    void Skip() {
        for (;;) {
            while (at_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[at_]))) ++at_;
            if (s_.substr(at_, 2) == "//") {
                while (at_ < s_.size() && s_[at_] != '\n') ++at_;
                continue;
            }
            // [$WIN32] style conditionals after a value: always true here
            if (at_ < s_.size() && s_[at_] == '[') {
                while (at_ < s_.size() && s_[at_] != ']') ++at_;
                if (at_ < s_.size()) ++at_;
                continue;
            }
            return;
        }
    }

    Token Quoted() {
        std::string out;
        ++at_;
        while (at_ < s_.size() && s_[at_] != '"') {
            char c = s_[at_++];
            if (c == '\\' && at_ < s_.size()) {
                char e = s_[at_++];
                out.push_back(e == 'n' ? '\n' : e == 't' ? '\t' : e);
            } else {
                out.push_back(c);
            }
        }
        if (at_ < s_.size()) ++at_;
        return {Token::Text, out};
    }

    std::string_view s_;
    std::size_t at_ = 0;
};

inline int Hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

inline std::string Squeezed(std::string_view s) {
    std::string out;
    for (char c : s)
        if (!std::isspace(static_cast<unsigned char>(c))) out.push_back(c);
    return out;
}

// "RVA|bytes" as Nova splits it: Convert.ToInt64(part, 16) takes a 0x in
// front, and the bytes are pairs of hex digits. Spaces between them are
// forgiven here, where Nova would have thrown mid-launch
inline bool Entry(std::string_view value, Patch& out) {
    std::size_t bar = value.find('|');
    if (bar == std::string_view::npos) return false;
    std::string rva = Squeezed(value.substr(0, bar));
    std::string_view rest = value.substr(bar + 1);
    std::string bytes = Squeezed(rest.substr(0, rest.find('|')));
    if (rva.size() > 2 && rva[0] == '0' && (rva[1] == 'x' || rva[1] == 'X')) rva = rva.substr(2);
    if (rva.empty() || rva.size() > 16 || bytes.empty() || bytes.size() % 2) return false;
    std::uint64_t at = 0;
    for (char c : rva) {
        int v = Hex(c);
        if (v < 0) return false;
        at = at << 4 | static_cast<std::uint64_t>(v);
    }
    std::vector<std::uint8_t> data;
    for (std::size_t i = 0; i < bytes.size(); i += 2) {
        int hi = Hex(bytes[i]), lo = Hex(bytes[i + 1]);
        if (hi < 0 || lo < 0) return false;
        data.push_back(static_cast<std::uint8_t>(hi << 4 | lo));
    }
    out.rva = at;
    out.bytes = std::move(data);
    return true;
}

inline bool AllDigits(const std::string& key) {
    for (char c : key)
        if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    return true;
}

// a value that's an object of its own, which isn't a patch at any depth
inline void SkipObject(Lexer& lex) {
    int depth = 1;
    while (depth > 0) {
        Token t = lex.Next();
        if (t.kind == Token::End) return;
        if (t.kind == Token::Open) ++depth;
        if (t.kind == Token::Close) --depth;
    }
}

}  // namespace detail

inline Parsed Parse(std::string_view text) {
    using namespace detail;
    Parsed parsed;
    // a UTF-8 BOM from Notepad is how plenty of these were saved
    if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF &&
        static_cast<unsigned char>(text[1]) == 0xBB && static_cast<unsigned char>(text[2]) == 0xBF)
        text.remove_prefix(3);
    Lexer lex(text);
    Token root = lex.Next();
    if (root.kind != Token::Text || lex.Next().kind != Token::Open) return parsed;
    for (;;) {
        Token key = lex.Next();
        if (key.kind != Token::Text) return parsed;
        Token value = lex.Next();
        if (value.kind == Token::Open) {
            SkipObject(lex);
            continue;
        }
        if (value.kind != Token::Text) return parsed;
        if (!AllDigits(key.text)) continue;
        Patch patch;
        if (Entry(value.text, patch))
            parsed.patches.push_back(std::move(patch));
        else
            parsed.skipped.push_back(key.text);
    }
}

// whether a patch lands wholly inside an image of `size` bytes
inline bool Inside(const Patch& patch, std::uint64_t size) {
    return patch.rva < size && patch.bytes.size() <= size - patch.rva;
}

}  // namespace ff13_nccp
