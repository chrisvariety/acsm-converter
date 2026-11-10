/*
  Browser-side removal of ADEPT (EBX_HANDLER) encryption from a PDF.

  Compiled to WebAssembly (see build.sh). It runs the very code the server
  runs -- vendor/libgourou/src/pdf_ebx.cpp on top of uPDFParser -- with the two
  primitives OpenSSL provides there (MD5, RC4) implemented below, so that the
  module needs no other library.

  The book key is not derived here: only the server can unwrap it (it takes the
  device's private key), and it hands the browser the 16 bytes for this one
  book.
*/

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <exception>
#include <string>
#include <vector>

#include <emscripten/emscripten.h>

#include <uPDFParser.h>
#include <pdf_ebx.h>

/* RFC 1321 */
static void md5(const unsigned char* msg, size_t len, unsigned char out[16])
{
    static const uint32_t K[64] = {
        0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,0xa8304613,0xfd469501,
        0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,0x6b901122,0xfd987193,0xa679438e,0x49b40821,
        0xf61e2562,0xc040b340,0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
        0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,0x676f02d9,0x8d2a4c8a,
        0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,
        0x289b7ec6,0xeaa127fa,0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
        0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,0xffeff47d,0x85845dd1,
        0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391};
    static const int R[64] = {
        7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,
        4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21};
    uint32_t h0=0x67452301, h1=0xefcdab89, h2=0x98badcfe, h3=0x10325476;

    std::vector<unsigned char> m(msg, msg+len);
    m.push_back(0x80);
    while (m.size() % 64 != 56)
        m.push_back(0);
    uint64_t bits = (uint64_t)len * 8;
    for (int i=0; i<8; i++)
        m.push_back((unsigned char)(bits >> (8*i)));

    for (size_t off=0; off<m.size(); off+=64)
    {
        uint32_t w[16];
        for (int i=0; i<16; i++)
            w[i] = m[off+i*4] | (m[off+i*4+1]<<8) | (m[off+i*4+2]<<16) | ((uint32_t)m[off+i*4+3]<<24);

        uint32_t a=h0, b=h1, c=h2, d=h3;
        for (int i=0; i<64; i++)
        {
            uint32_t f;
            int g;
            if (i<16)      { f = (b&c)|(~b&d); g = i; }
            else if (i<32) { f = (d&b)|(~d&c); g = (5*i+1)%16; }
            else if (i<48) { f = b^c^d;        g = (3*i+5)%16; }
            else           { f = c^(b|~d);     g = (7*i)%16; }
            uint32_t tmp = d;
            d = c;
            c = b;
            uint32_t x = a + f + K[i] + w[g];
            b = b + ((x<<R[i]) | (x>>(32-R[i])));
            a = tmp;
        }
        h0+=a; h1+=b; h2+=c; h3+=d;
    }

    uint32_t h[4] = {h0, h1, h2, h3};
    for (int i=0; i<4; i++)
        for (int j=0; j<4; j++)
            out[i*4+j] = (unsigned char)(h[i] >> (8*j));
}

class PlainEBXCrypto : public gourou::EBXCrypto
{
public:
    virtual void md5(const unsigned char* data, unsigned int length, unsigned char* digestOut)
    {
        ::md5(data, length, digestOut);
    }

    virtual void rc4(const unsigned char* key, unsigned int keyLength,
                     const unsigned char* dataIn, unsigned int length, unsigned char* dataOut)
    {
        unsigned char s[256];
        for (int i=0; i<256; i++)
            s[i] = i;
        unsigned char j = 0;
        for (int i=0; i<256; i++)
        {
            j = j + s[i] + key[i % keyLength];
            unsigned char t = s[i]; s[i] = s[j]; s[j] = t;
        }
        unsigned char a = 0, b = 0;
        for (unsigned int n=0; n<length; n++)
        {
            a++;
            b += s[a];
            unsigned char t = s[a]; s[a] = s[b]; s[b] = t;
            dataOut[n] = dataIn[n] ^ s[(unsigned char)(s[a] + s[b])];
        }
    }
};

static int hexDigit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Result codes, mirrored in scripts/static/pdf-worker.js */
enum {
    PDF_OK = 0,
    PDF_BAD_KEY = 1,        /* Key is not 32 hexadecimal digits */
    PDF_NOT_ADEPT = 2,      /* Not a PDF, or one without an EBX_HANDLER: the wrong file */
    PDF_UNSUPPORTED = 3,    /* EBX_HANDLER version other than 4 */
    PDF_FAILED = 4          /* Anything else (a parser exception, a write error) */
};

extern "C" EMSCRIPTEN_KEEPALIVE
int pdf_decrypt(const char* inPath, const char* outPath, const char* keyHex)
{
    unsigned char bookKey[16];

    if (strlen(keyHex) != 32)
        return PDF_BAD_KEY;
    for (int i=0; i<16; i++)
    {
        int high = hexDigit(keyHex[i*2]), low = hexDigit(keyHex[i*2+1]);
        if (high < 0 || low < 0)
            return PDF_BAD_KEY;
        bookKey[i] = (unsigned char)((high << 4) | low);
    }

    try
    {
        uPDFParser::Parser parser;

        try
        {
            parser.parse(inPath);
        }
        catch (std::exception& e)
        {
            fprintf(stderr, "pdf_decrypt: parse: %s\n", e.what());
            return PDF_NOT_ADEPT;
        }

        uPDFParser::Object* ebx = gourou::findEBXHandler(parser);
        if (!ebx)
            return PDF_NOT_ADEPT;

        if (!ebx->hasKey("V") || (*ebx)["V"]->type() != uPDFParser::DataType::INTEGER ||
            ((uPDFParser::Integer*)(*ebx)["V"])->value() != 4)
            return PDF_UNSUPPORTED;

        PlainEBXCrypto crypto;
        gourou::removeEBXEncryption(parser, ebx, bookKey, sizeof(bookKey), crypto);

        /* Parser::write() appends */
        unlink(outPath);
        parser.write(outPath);
    }
    catch (std::exception& e)
    {
        fprintf(stderr, "pdf_decrypt: %s\n", e.what());
        return PDF_FAILED;
    }

    return PDF_OK;
}
