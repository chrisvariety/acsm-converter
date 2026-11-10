/*
  Copyright (c) 2021, Grégory Soutadé

  All rights reserved.
  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions are met:

  * Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.
  * Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.
  * Neither the name of the copyright holder nor the
  names of its contributors may be used to endorse or promote products
  derived from this software without specific prior written permission.

  THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND ANY
  EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
  DISCLAIMED. IN NO EVENT SHALL THE REGENTS AND CONTRIBUTORS BE LIABLE FOR ANY
  DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
  (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
  ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

#include <getopt.h>
#include <libgen.h>

#include <iostream>
#include <fstream>
#include <stdexcept>
#include <cstring>
#include <cstdio>
#include <algorithm>

#include <libgourou.h>
#include <libgourou_common.h>

#include "drmprocessorclientimpl.h"
#include "utils_common.h"

static const char* deviceFile     = "device.xml";
static const char* activationFile = "activation.xml";
static const char* devicekeyFile  = "devicesalt";
static const char* acsmFile       = 0;
static       bool  exportPrivateKey = false;
static const char* outputFile     = 0;
static const char* outputDir      = 0;
static       bool  resume         = false;
static       bool  notify         = true;
// Local patch (not upstream): split fulfill and download across two runs, so
// a book whose download was refused can be fetched by other means (e.g. the
// user's own browser) and finished later without fulfilling it again.
static const char* saveFulfillment = 0;  // write the fulfill reply here
static const char* fromFile        = 0;  // already-downloaded encrypted book
static const char* fulfillmentFile = 0;  // fulfill reply saved by an earlier run
static       bool  bookKey         = false;  // print the book key of fulfillmentFile

enum { OPT_SAVE_FULFILLMENT = 1000, OPT_FROM_FILE, OPT_FULFILLMENT, OPT_BOOK_KEY };


class ACSMDownloader
{
public:

    int run()
    {
        int ret = 0;
        try
        {
            gourou::DRMProcessor processor(&client, deviceFile, activationFile, devicekeyFile);
            gourou::User* user = processor.getUser();
    
            if (exportPrivateKey)
            {
                std::string filename;
                if (outputFile)
                    filename = outputFile;
                else
                {
                    filename = std::string("Adobe_PrivateLicenseKey--") + user->getUsername() + ".der";

                    if (outputDir)
                        filename = std::string(outputDir) + "/" + filename;
                }

                createPath(filename.c_str());
                
                processor.exportPrivateLicenseKey(filename);

                std::cout << "Private license key exported to " << filename << std::endl;
            }
            else if (bookKey)
            {
                // For a book that is decrypted elsewhere (in the user's
                // browser): everything --from-file would have used, minus the
                // book. The name is the one --from-file would give the file,
                // before its extension.
                pugi::xml_document fulfillDoc;
                if (!fulfillDoc.load_file(fulfillmentFile))
                    throw std::runtime_error(std::string("Invalid fulfillment file ") + fulfillmentFile);
                gourou::FulfillmentItem item(fulfillDoc, user);

                unsigned char key[16];
                processor.getBookKey(&item, key);

                std::string name = item.getMetadata("title");
                if (name == "")
                    name = "output";
                else
                    std::replace(name.begin(), name.end(), '/', '_');

                char hex[sizeof(key)*2+1];
                for (unsigned i=0; i<sizeof(key); i++)
                    snprintf(&hex[i*2], 3, "%02x", key[i]);

                std::cout << "Book key " << hex << std::endl;
                std::cout << "Book name " << name << std::endl;
            }
            else
            {
                gourou::FulfillmentItem* item;
                pugi::xml_document fulfillDoc;  // FulfillmentItem keeps its own copy

                if (fromFile)
                {
                    if (!fulfillDoc.load_file(fulfillmentFile))
                        throw std::runtime_error(std::string("Invalid fulfillment file ") + fulfillmentFile);
                    item = new gourou::FulfillmentItem(fulfillDoc, user);
                }
                else
                {
                    item = processor.fulfill(acsmFile, notify);

                    // Saved before downloading, so it survives a refused download.
                    if (saveFulfillment)
                        gourou::writeFile(saveFulfillment, item->getFulfillmentXML());
                }

                std::string filename;
                if (outputFile)
                    filename = outputFile;
                else
                {
                    filename = item->getMetadata("title");
                    if (filename == "")
                        filename = "output";
                    else
                    {
                        // Remove invalid characters
                        std::replace(filename.begin(), filename.end(), '/', '_');
                    }

                    if (outputDir)
                        filename = std::string(outputDir) + "/" + filename;
                }
    
                createPath(filename.c_str());

                gourou::DRMProcessor::ITEM_TYPE type;
                if (fromFile)
                {
                    std::ifstream in(fromFile, std::ios::binary);
                    if (!in)
                        throw std::runtime_error(std::string("Unable to open ") + fromFile);
                    // No Content-Type header to go on, so sniff the magic the
                    // way the server's header would have told us.
                    char magic[5] = {0};
                    in.read(magic, sizeof(magic));
                    std::string contentType =
                        (in.gcount() == 5 && !memcmp(magic, "%PDF-", 5)) ? "application/pdf" : "";
                    in.clear();
                    in.seekg(0);
                    {
                        std::ofstream out(filename, std::ios::binary | std::ios::trunc);
                        out << in.rdbuf();
                        if (!out)
                            throw std::runtime_error("Unable to write " + filename);
                    }
                    type = processor.addRightsToFile(item, filename, contentType);
                }
                else
                    type = processor.download(item, filename, resume);

                if (!outputFile)
                {
                    std::string finalName = filename;
                    if (type == gourou::DRMProcessor::ITEM_TYPE::PDF)
                        finalName += ".pdf";
                    else
                        finalName += ".epub";
                    rename(filename.c_str(), finalName.c_str());
                    filename = finalName;
                }
                std::cout << "Created " << filename << std::endl;

                serializeLoanToken(item);
            }
        } catch(std::exception& e)
        {
            std::cout << e.what() << std::endl;
            ret = 1;
        }

        return ret;
    }

    void serializeLoanToken(gourou::FulfillmentItem* item)
    {
        gourou::LoanToken* token = item->getLoanToken();

        // No loan token available
        if (!token)
            return;

        pugi::xml_document doc;

        pugi::xml_node decl = doc.append_child(pugi::node_declaration);
        decl.append_attribute("version") = "1.0";

        pugi::xml_node root = doc.append_child("loanToken");
        gourou::appendTextElem(root, "id",          (*token)["id"]);
        gourou::appendTextElem(root, "operatorURL", (*token)["operatorURL"]);
        gourou::appendTextElem(root, "validity",    (*token)["validity"]);
        gourou::appendTextElem(root, "name",        item->getMetadata("title"));

        char * activationDir = strdup(deviceFile);
        activationDir = dirname(activationDir);
        
        gourou::StringXMLWriter xmlWriter;
        doc.save(xmlWriter, "  ");
        std::string xmlStr = xmlWriter.getResult();

        // Use first bytes of SHA1(id) as filename
        unsigned char sha1[gourou::SHA1_LEN];
        client.digest("SHA1", (unsigned char*)(*token)["id"].c_str(), (*token)["id"].size(), sha1);
        gourou::ByteArray tmp(sha1, sizeof(sha1));
        std::string filenameHex = tmp.toHex();
        std::string filename(filenameHex.c_str(), ID_HASH_SIZE);
        std::string fullPath = std::string(activationDir);
        fullPath += std::string ("/") + std::string(LOANS_DIR);
        mkpath(fullPath.c_str());
        fullPath += filename + std::string(".xml");
        gourou::writeFile(fullPath, xmlStr);

        std::cout << "Loan token serialized into " << fullPath << std::endl;

        free(activationDir);
    }

private:
    DRMProcessorClientImpl client;
};    


static void usage(const char* cmd)
{
    std::cout << basename((char*)cmd) << " download EPUB file from ACSM request file" << std::endl << std::endl;
    std::cout << "Usage: " << basename((char*)cmd) << " [OPTIONS] file.acsm" << std::endl << std::endl;
    std::cout << "Global Options:" << std::endl;
    std::cout << "  " << "-O|--output-dir"      << "\t"   << "Optional output directory were to put result (default ./) (not compatible with -o)" << std::endl;
    std::cout << "  " << "-o|--output-file"     << "\t"   << "Optional output filename (default <title.(epub|pdf|der)>) (not compatible with -O)" << std::endl;
    std::cout << "  " << "-f|--acsm-file"       << "\t"   << "Backward compatibility: ACSM request file for epub download" << std::endl;
    std::cout << "  " << "-e|--export-private-key"<< "\t" << "Export private key in DER format" << std::endl;
    std::cout << "  " << "-r|--resume"          << "\t\t" << "Try to resume download (in case of previous failure)" << std::endl;
    std::cout << "  " << "-N|--no-notify"       << "\t\t" << "Don't notify server, even if requested" << std::endl;
    std::cout << "  " << "--save-fulfillment"   << "\t"   << "Write the fulfill reply to this file before downloading" << std::endl;
    std::cout << "  " << "--from-file"          << "\t\t" << "Skip fulfill+download; finish this already-downloaded file (needs --fulfillment, no ACSM)" << std::endl;
    std::cout << "  " << "--fulfillment"        << "\t\t" << "Fulfill reply saved by --save-fulfillment, used with --from-file or --book-key" << std::endl;
    std::cout << "  " << "--book-key"           << "\t\t" << "Print the key and name of the book --fulfillment describes; no ACSM, no download" << std::endl;
    std::cout << "  " << "-v|--verbose"         << "\t\t" << "Increase verbosity, can be set multiple times" << std::endl;
    std::cout << "  " << "-V|--version"         << "\t\t" << "Display libgourou version" << std::endl;
    std::cout << "  " << "-h|--help"            << "\t\t" << "This help" << std::endl;

    std::cout << "ADEPT Options:" << std::endl;
    std::cout << "  " << "-D|--adept-directory" << "\t"   << ".adept directory that must contains device.xml, activation.xml and devicesalt" << std::endl;
    std::cout << "  " << "-d|--device-file"     << "\t"   << "device.xml file from eReader" << std::endl;
    std::cout << "  " << "-a|--activation-file" << "\t"   << "activation.xml file from eReader" << std::endl;
    std::cout << "  " << "-k|--device-key-file" << "\t"   << "private device key file (eg devicesalt/devkey.bin) from eReader" << std::endl;

    std::cout << std::endl;

    std::cout << "Environment:" << std::endl;
    std::cout << "Device file, activation file and device key file are optionals. If not set, they are looked into :" << std::endl << std::endl;
    std::cout << "  * $ADEPT_DIR environment variable" << std::endl;
    std::cout << "  * /home/<user>/.config/adept" << std::endl;
    std::cout << "  * Current directory" << std::endl;
    std::cout << "  * .adept" << std::endl;
    std::cout << "  * adobe-digital-editions directory" << std::endl;
    std::cout << "  * .adobe-digital-editions directory" << std::endl;
}

int main(int argc, char** argv)
{
    int c, ret = -1;
    std::string _deviceFile, _activationFile, _devicekeyFile;

    const char** files[] = {&devicekeyFile, &deviceFile, &activationFile};
    int verbose = gourou::DRMProcessor::getLogLevel();

    while (1) {
        int option_index = 0;
        static struct option long_options[] = {
            {"adept-directory",  required_argument, 0,  'D' },
            {"device-file",      required_argument, 0,  'd' },
            {"activation-file",  required_argument, 0,  'a' },
            {"device-key-file",  required_argument, 0,  'k' },
            {"output-dir",       required_argument, 0,  'O' },
            {"output-file",      required_argument, 0,  'o' },
            {"acsm-file",        required_argument, 0,  'f' },
            {"export-private-key",no_argument,      0,  'e' },
            {"resume",           no_argument,       0,  'r' },
            {"no-notify",        no_argument,       0,  'N' },
            {"verbose",          no_argument,       0,  'v' },
            {"version",          no_argument,       0,  'V' },
            {"help",             no_argument,       0,  'h' },
            {"save-fulfillment", required_argument, 0,  OPT_SAVE_FULFILLMENT },
            {"from-file",        required_argument, 0,  OPT_FROM_FILE },
            {"fulfillment",      required_argument, 0,  OPT_FULFILLMENT },
            {"book-key",         no_argument,       0,  OPT_BOOK_KEY },
            {0,                  0,                 0,  0 }
        };

        c = getopt_long(argc, argv, "D:d:a:k:O:o:f:erNvVh",
                        long_options, &option_index);
        if (c == -1)
            break;

        switch (c) {
        case 'D':
            _deviceFile = std::string(optarg) + "/device.xml";
            _activationFile = std::string(optarg) + "/activation.xml";
            _devicekeyFile = std::string(optarg) + "/devicesalt";
            deviceFile = _deviceFile.c_str();
            activationFile = _activationFile.c_str();
            devicekeyFile = _devicekeyFile.c_str();
            break;
        case 'd':
            deviceFile = optarg;
            break;
        case 'a':
            activationFile = optarg;
            break;
        case 'k':
            devicekeyFile = optarg;
            break;
        case 'f':
            acsmFile = optarg;
            break;
        case 'O':
            outputDir = optarg;
            break;
        case 'o':
            outputFile = optarg;
            break;
        case 'e':
            exportPrivateKey = true;
            break;
        case 'r':
            resume = true;
            break;
        case 'N':
            notify = false;
            break;
        case 'v':
            verbose++;
            break;
        case 'V':
            version();
            return 0;
        case 'h':
            usage(argv[0]);
            return 0;
        case OPT_SAVE_FULFILLMENT:
            saveFulfillment = optarg;
            break;
        case OPT_FROM_FILE:
            fromFile = optarg;
            break;
        case OPT_FULFILLMENT:
            fulfillmentFile = optarg;
            break;
        case OPT_BOOK_KEY:
            bookKey = true;
            break;
        default:
            usage(argv[0]);
            return -1;
        }
    }

    gourou::DRMProcessor::setLogLevel(verbose);

    if (optind == argc-1)
        acsmFile = argv[optind];

    if ((!acsmFile && !exportPrivateKey && !fromFile && !bookKey) || (outputDir && !outputDir[0]) ||
        (outputFile && !outputFile[0]))
    {
        usage(argv[0]);
        return -1;
    }

    // So does --book-key, which needs nothing but the fulfill reply.
    if (bookKey && (!fulfillmentFile || acsmFile || exportPrivateKey || saveFulfillment || fromFile))
    {
        usage(argv[0]);
        return -1;
    }

    // --from-file replaces the ACSM entirely; it is a separate mode.
    if (fromFile && (!fulfillmentFile || acsmFile || exportPrivateKey || saveFulfillment))
    {
        usage(argv[0]);
        return -1;
    }

    if (outputDir && outputFile)
    {
        std::cout << "Error : you cannot use both -o and -O" << std::endl;
        return -1;
    }

    ACSMDownloader downloader;

    int i;
    bool hasErrors = false;
    const char* orig;
    for (i=0; i<(int)ARRAY_SIZE(files); i++)
    {
        orig = *files[i];
        *files[i] = findFile(*files[i]);
        if (!*files[i])
        {
            std::cout << "Error : " << orig << " doesn't exists, did you activate your device ?" << std::endl;
            ret = -1;
            hasErrors = true;
        }
    }

    if (hasErrors)
        goto end;

    if (exportPrivateKey)
    {
        if (acsmFile)
        {
            usage(argv[0]);
            return -1;
        }
    }
    else if (bookKey)
    {
        if (!pathExists(fulfillmentFile))
        {
            std::cout << "Error : " << fulfillmentFile << " doesn't exists" << std::endl;
            ret = -1;
            goto end;
        }
    }
    else if (fromFile)
    {
        if (!pathExists(fromFile) || !pathExists(fulfillmentFile))
        {
            std::cout << "Error : " << fromFile << " or " << fulfillmentFile << " doesn't exists" << std::endl;
            ret = -1;
            goto end;
        }
    }
    else
    {
        if (!pathExists(acsmFile))
        {
            std::cout << "Error : " << acsmFile << " doesn't exists" << std::endl;
            ret = -1;
            goto end;
        }
    }

    ret = downloader.run();

end:
    for (i=0; i<(int)ARRAY_SIZE(files); i++)
    {
        if (*files[i])
            free((void*)*files[i]);
    }

    return ret;
}
