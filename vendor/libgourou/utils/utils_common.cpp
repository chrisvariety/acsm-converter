/*
  Copyright (c) 2022, Grégory Soutadé

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

#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <limits.h>
#include <libgen.h>

#include <iostream>

#include <libgourou.h>
#include <libgourou_common.h>
#include "utils_common.h"

static const char* defaultDirs[]  = {
    ".adept/",
    "./adobe-digital-editions/",
    "./.adobe-digital-editions/"
};

void version(void)
{
    std::cout << "Current libgourou version : " << gourou::DRMProcessor::VERSION << std::endl ;
}

bool pathExists(const char* path)
{
    struct stat _stat;
    int ret = stat(path, &_stat);

    return (ret == 0);
}

const char* findFile(const char* filename, bool inDefaultDirs)
{
    std::string path;

    const char* adeptDir = getenv("ADEPT_DIR");
    if (adeptDir && adeptDir[0])
    {
        path = adeptDir + std::string("/") + filename;
        if (pathExists(path.c_str()))
            return strdup(path.c_str());
    }

    path = gourou::DRMProcessor::getDefaultAdeptDir() + filename;
    if (pathExists(path.c_str()))
        return strdup(path.c_str());

    if (pathExists(filename))
        return strdup(filename);

    if (!inDefaultDirs) return 0;

    for (int i=0; i<(int)ARRAY_SIZE(defaultDirs); i++)
    {
        path = std::string(defaultDirs[i]) + filename;
        if (pathExists(path.c_str()))
            return strdup(path.c_str());
    }

    return 0;
}

// https://stackoverflow.com/questions/2336242/recursive-mkdir-system-call-on-unix
void mkpath(const char *dir)
{
    char tmp[PATH_MAX];
    char *p = NULL;
    size_t len;

    snprintf(tmp, sizeof(tmp),"%s",dir);
    len = strlen(tmp);
    if (tmp[len - 1] == '/')
        tmp[len - 1] = 0;
    for (p = tmp + 1; *p; p++)
        if (*p == '/') {
            *p = 0;
            mkdir(tmp, S_IRWXU);
            *p = '/';
        }
    mkdir(tmp, S_IRWXU);
}

void fileCopy(const char* in, const char* out)
{
    char buffer[4096], *_buffer;
    int ret, ret2, fdIn, fdOut;

    fdIn = open(in, O_RDONLY);

    if (!fdIn)
        EXCEPTION(gourou::CLIENT_FILE_ERROR, "Unable to open " << in);

    fdOut = gourou::createNewFile(out);

    if (!fdOut)
    {
        close (fdIn);
        EXCEPTION(gourou::CLIENT_FILE_ERROR, "Unable to open " << out);
    }

    while (true)
    {
        ret = ::read(fdIn, buffer, sizeof(buffer));
        if (ret <= 0)
            break;
        do
        {
            _buffer = buffer;
            ret2 = ::write(fdOut, _buffer, ret);
            if (ret2 >= 0)
            {
                ret -= ret2;
                _buffer += ret2;
            }
            else
            {
                EXCEPTION(gourou::CLIENT_FILE_ERROR, "Error writing " << out);
            }
        } while (ret);
    }

    close (fdIn);
    close (fdOut);
}

void createPath(const char* filename)
{
    char* basepath = strdup(filename);
    char* outputDir = dirname(basepath);
    if (outputDir && !pathExists(outputDir))
        mkpath(outputDir);
    free(basepath);
}
