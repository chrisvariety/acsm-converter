/*
  See pdf_ebx.h. Depends on uPDFParser and the C++ standard library only.

  This file is part of libgourou's vendored copy; same license (LGPL v3+).
*/

#include <stdint.h>
#include <string.h>
#include <map>
#include <string>
#include <vector>

#include <pdf_ebx.h>

namespace gourou
{
    /**
     * @brief Decode a PDF literal string body (ISO 32000-1 7.3.4.2)
     *
     * uPDFParser keeps the text between the parentheses as it is in the file.
     * Its own String::unescapedValue() knows \\, \(, \), \n and \r only, so a
     * ciphertext byte written as an octal escape (\ddd) or as \t, \b, \f was
     * fed to RC4 as its escape sequence and came out as garbage.
     */
    static std::string unescapePDFString(const std::string& raw)
    {
        std::string res;
        res.reserve(raw.size());

        for (size_t i=0; i<raw.size(); i++)
        {
            char c = raw[i];

            /* An unescaped end of line is a single \n, however it is written */
            if (c == '\r')
            {
                if (i+1 < raw.size() && raw[i+1] == '\n')
                    i++;
                res += '\n';
                continue;
            }

            if (c != '\\' || i+1 >= raw.size())
            {
                res += c;
                continue;
            }

            c = raw[++i];
            switch (c)
            {
            case 'n': res += '\n'; break;
            case 'r': res += '\r'; break;
            case 't': res += '\t'; break;
            case 'b': res += '\b'; break;
            case 'f': res += '\f'; break;
            case '\r': /* Line continuation */
                if (i+1 < raw.size() && raw[i+1] == '\n')
                    i++;
                break;
            case '\n':
                break;
            default:
                if (c >= '0' && c <= '7')
                {
                    unsigned int value = c - '0';
                    for (int digits=1; digits<3 && i+1 < raw.size() &&
                             raw[i+1] >= '0' && raw[i+1] <= '7'; digits++)
                        value = (value << 3) + (raw[++i] - '0');
                    res += (char)(value & 0xff);
                }
                else
                    res += c; /* \( \) \\ and anything else: drop the backslash */
            }
        }

        return res;
    }

    static int hexDigit(char c)
    {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }

    /* White space is allowed between digits, and a missing last digit is 0 */
    static std::string fromHex(const std::string& hex)
    {
        std::string res;
        int high = -1;

        for (size_t i=0; i<hex.size(); i++)
        {
            int digit = hexDigit(hex[i]);
            if (digit < 0)
                continue;
            if (high < 0)
                high = digit;
            else
            {
                res += (char)((high << 4) | digit);
                high = -1;
            }
        }
        if (high >= 0)
            res += (char)(high << 4);

        return res;
    }

    static std::string toHex(const std::string& data)
    {
        static const char digits[] = "0123456789abcdef";
        std::string res;
        res.reserve(data.size()*2);

        for (size_t i=0; i<data.size(); i++)
        {
            res += digits[((unsigned char)data[i]) >> 4];
            res += digits[((unsigned char)data[i]) & 0xf];
        }

        return res;
    }

    /**
     * @brief Decrypt every string held by a PDF value, recursing into arrays
     * and dictionaries
     *
     * Strings are encrypted wherever they sit in an object -- /Dest arrays,
     * /A << /D (...) >> actions, name trees -- not only at the top level of
     * its dictionary.
     *
     * @return The clear replacement when data is itself a string (the caller
     * swaps it in and deletes data), 0 otherwise
     */
    static uPDFParser::DataType* decryptStrings(uPDFParser::DataType* data,
                                                const unsigned char* key, unsigned int keyLength,
                                                EBXCrypto& crypto)
    {
        switch (data->type())
        {
        case uPDFParser::DataType::STRING:
        case uPDFParser::DataType::HEXASTRING:
        {
            std::string encrypted;

            if (data->type() == uPDFParser::DataType::STRING)
                encrypted = unescapePDFString(((uPDFParser::String*) data)->value());
            else
                encrypted = fromHex(((uPDFParser::HexaString*) data)->value());

            if (encrypted.empty())
                return 0;

            std::string clear(encrypted.size(), '\0');
            crypto.rc4(key, keyLength,
                       (const unsigned char*)encrypted.data(), encrypted.size(),
                       (unsigned char*)&clear[0]);

            /* Always written back as a hexadecimal string: the clear text can
               hold any byte, and String::str() escapes parentheses only. */
            return new uPDFParser::HexaString(toHex(clear));
        }
        case uPDFParser::DataType::ARRAY:
        {
            std::vector<uPDFParser::DataType*>& values = ((uPDFParser::Array*) data)->value();

            for (size_t i=0; i<values.size(); i++)
            {
                uPDFParser::DataType* decrypted = decryptStrings(values[i], key, keyLength, crypto);
                if (decrypted)
                {
                    delete values[i];
                    values[i] = decrypted;
                }
            }
            return 0;
        }
        case uPDFParser::DataType::DICTIONARY:
        {
            std::map<std::string, uPDFParser::DataType*>& values = ((uPDFParser::Dictionary*) data)->value();
            std::map<std::string, uPDFParser::DataType*>::iterator it;

            for (it = values.begin(); it != values.end(); it++)
            {
                if (!it->second)
                    continue;
                uPDFParser::DataType* decrypted = decryptStrings(it->second, key, keyLength, crypto);
                if (decrypted)
                {
                    delete it->second;
                    it->second = decrypted;
                }
            }
            return 0;
        }
        default:
            return 0;
        }
    }

    uPDFParser::Object* findEBXHandler(uPDFParser::Parser& parser)
    {
        std::vector<uPDFParser::Object*>& objects = parser.objects();
        std::vector<uPDFParser::Object*>::reverse_iterator rIt;

        for (rIt = objects.rbegin(); rIt != objects.rend(); rIt++)
        {
            if ((*rIt)->hasKey("Filter") && (**rIt)["Filter"]->str() == "/EBX_HANDLER")
                return *rIt;
        }

        return 0;
    }

    void removeEBXEncryption(uPDFParser::Parser& parser, uPDFParser::Object* ebx,
                             const unsigned char* bookKey, unsigned int bookKeyLength,
                             EBXCrypto& crypto)
    {
        /* A copy: objects are removed from the parser's own list below */
        std::vector<uPDFParser::Object*> objects = parser.objects();
        std::vector<uPDFParser::Object*>::iterator it, ebxIt;
        std::vector<uPDFParser::Object*> ebxObjects;
        int ebxId = ebx->objectId();

        for (it = objects.begin(); it != objects.end(); it++)
        {
            uPDFParser::Object* object = *it;

            if (object->objectId() == ebxId)
            {
                ebxObjects.push_back(object);
                continue;
            }

            // Should not decrypt XRef stream
            if (object->hasKey("Type") && (*object)["Type"]->str() == "/XRef")
                continue;

            /* Object key: MD5(book key | object id (3 bytes LE) | generation (2 bytes LE)) */
            std::string toHash((const char*)bookKey, bookKeyLength);
            uint32_t objectId = object->objectId();
            uint32_t generationNumber = object->generationNumber();
            for (int i=0; i<3; i++)
                toHash += (char)((objectId >> (8*i)) & 0xff);
            for (int i=0; i<2; i++)
                toHash += (char)((generationNumber >> (8*i)) & 0xff);

            unsigned char objectKey[16];
            crypto.md5((const unsigned char*)toHash.data(), toHash.size(), objectKey);

            decryptStrings(&object->dictionary(), objectKey, sizeof(objectKey), crypto);

            std::vector<uPDFParser::DataType*>& datas = object->data();

            for (size_t i=0; i<datas.size(); i++)
            {
                if (datas[i]->type() == uPDFParser::DataType::STREAM)
                {
                    uPDFParser::Stream* stream = (uPDFParser::Stream*) datas[i];
                    unsigned char* encryptedData = stream->data();
                    unsigned int dataLength = stream->dataLength();
                    unsigned char* clearData = new unsigned char[dataLength];

                    crypto.rc4(objectKey, sizeof(objectKey), encryptedData, dataLength, clearData);

                    stream->setData(clearData, dataLength, true);
                    continue;
                }

                /* An object that is a bare string or array, not a dictionary */
                uPDFParser::DataType* decrypted = decryptStrings(datas[i], objectKey, sizeof(objectKey), crypto);
                if (decrypted)
                {
                    delete datas[i];
                    datas[i] = decrypted;
                }
            }
        }

        /* Delete objects that reference EBX objects,
           except in trailer and objects that references Info */
        for (it = objects.begin(); it != objects.end(); it++)
        {
            uPDFParser::Object* object = *it;

            if (object->hasKey("Encrypt") && (*object)["Encrypt"]->type() == uPDFParser::DataType::REFERENCE)
            {
                uPDFParser::Reference* encrypt = (uPDFParser::Reference*)(*object)["Encrypt"];

                if (object->hasKey("Info"))
                {
                    object->deleteKey("Encrypt");
                    continue;
                }

                /* Delete EBX objects */
                for (ebxIt = ebxObjects.begin(); ebxIt != ebxObjects.end(); ebxIt++)
                {
                    if (encrypt->value() == (*ebxIt)->objectId())
                    {
                        parser.removeObject(object);
                        break;
                    }
                }
            }
        }

        /* Delete EBX objects */
        for (it = ebxObjects.begin(); it != ebxObjects.end(); it++)
            parser.removeObject(*it);

        uPDFParser::Object& trailer = parser.getTrailer();
        trailer.deleteKey("Encrypt");
    }
}
