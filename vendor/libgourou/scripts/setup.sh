#!/bin/bash

# uPDFParser
if [ ! -d lib/updfparser ] ; then
    git clone https://forge.soutade.fr/soutade/uPDFParser.git lib/updfparser
    pushd lib/updfparser
    make BUILD_STATIC=1 BUILD_SHARED=0
    popd
fi
