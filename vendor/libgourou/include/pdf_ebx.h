/*
  Removal of EBX_HANDLER (ADEPT) encryption from a parsed PDF, once the book
  key is known.

  Split out of DRMProcessor::removePDFDRM (libgourou.cpp) so the same code runs
  in two places: on the server, driven by libgourou with OpenSSL behind it, and
  in the browser, compiled to WebAssembly with nothing but uPDFParser (see
  wasm/ at the top of the repository). For that reason this file and
  pdf_ebx.cpp depend on uPDFParser and the C++ standard library only: no
  pugixml, no OpenSSL, no libgourou_common.h.

  This file is part of libgourou's vendored copy; same license (LGPL v3+).
*/

#ifndef _PDF_EBX_H_
#define _PDF_EBX_H_

#include <uPDFParser.h>

namespace gourou
{
    /**
     * @brief The two primitives EBX_HANDLER needs, supplied by the caller
     */
    class EBXCrypto
    {
    public:
        virtual ~EBXCrypto() {}

        /** @param digestOut 16 bytes */
        virtual void md5(const unsigned char* data, unsigned int length,
                         unsigned char* digestOut) = 0;

        /** @param dataOut Same length as dataIn */
        virtual void rc4(const unsigned char* key, unsigned int keyLength,
                         const unsigned char* dataIn, unsigned int length,
                         unsigned char* dataOut) = 0;
    };

    /**
     * @brief Find the PDF's EBX_HANDLER encryption dictionary
     *
     * @return The last object whose /Filter is /EBX_HANDLER, 0 if there is none
     */
    uPDFParser::Object* findEBXHandler(uPDFParser::Parser& parser);

    /**
     * @brief Decrypt every string and stream of the PDF with the book key,
     * then drop the encryption dictionary and the references to it
     *
     * Only EBX_HANDLER /V 4 is implemented; the caller checks the version.
     *
     * @param ebx        Object returned by findEBXHandler()
     * @param bookKey    Decrypted book key (16 bytes)
     */
    void removeEBXEncryption(uPDFParser::Parser& parser, uPDFParser::Object* ebx,
                             const unsigned char* bookKey, unsigned int bookKeyLength,
                             EBXCrypto& crypto);
}

#endif
