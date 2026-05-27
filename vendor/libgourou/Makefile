PREFIX ?= /usr/local
LIBDIR ?= /lib
INCDIR ?= /include

AR ?= $(CROSS)ar
CXX ?= $(CROSS)g++

UPDFPARSERLIB = ./lib/updfparser/libupdfparser.a

CXXFLAGS += -Wall -fPIC -I./include -I./usr/include/pugixml -I./lib/updfparser/include
LDFLAGS = -lpugixml

VERSION     := $(shell cat include/libgourou.h |grep LIBGOUROU_VERSION|cut -d '"' -f2)

UNAME := $(shell uname -s)

BUILD_STATIC ?= 0
BUILD_SHARED ?= 1
BUILD_UTILS  ?= 1

TARGETS =
TARGET_LIBRARIES =
ifneq ($(STATIC_UTILS),)
  BUILD_STATIC=1
endif
ifneq ($(BUILD_STATIC), 0)
  TARGETS += libgourou.a
  TARGET_LIBRARIES += libgourou.a
  STATIC_UTILS=1
endif
ifneq ($(BUILD_SHARED), 0)
  ifeq ($(UNAME), Darwin)
	TARGETS += libgourou.dylib
	TARGET_LIBRARIES += libgourou.dylib libgourou.dylib.$(VERSION)
  else
	TARGETS += libgourou.so
	TARGET_LIBRARIES += libgourou.so libgourou.so.$(VERSION)
  endif
endif
ifneq ($(BUILD_UTILS), 0)
  TARGETS += build_utils
endif


ifneq ($(DEBUG),)
CXXFLAGS += -ggdb -O0 -DDEBUG
else
CXXFLAGS += -O2
endif

ifneq ($(STATIC_NONCE),)
CXXFLAGS += -DSTATIC_NONCE=1
endif

SRCDIR      := src
BUILDDIR    := obj
SRCEXT      := cpp
OBJEXT      := o

SOURCES      = src/libgourou.cpp src/user.cpp src/device.cpp src/fulfillment_item.cpp src/loan_token.cpp src/bytearray.cpp
OBJECTS     := $(patsubst $(SRCDIR)/%,$(BUILDDIR)/%,$(SOURCES:.$(SRCEXT)=.$(OBJEXT)))

all: version lib obj $(TARGETS)

version:
	@echo "Building libgourou $(VERSION)"

lib:
	mkdir lib
	./scripts/setup.sh

update_lib:
	./scripts/update_lib.sh

obj:
	mkdir obj

$(BUILDDIR)/%.$(OBJEXT): $(SRCDIR)/%.$(SRCEXT)
	$(CXX) $(CXXFLAGS) -c $^ -o $@

libgourou: $(TARGET_LIBRARIES)

libgourou.a: $(OBJECTS) $(UPDFPARSERLIB)
	$(AR) rcs --thin $@ $^

libgourou.so.$(VERSION): $(OBJECTS) $(UPDFPARSERLIB)
	$(CXX) $^ -Wl,-soname,$@ $(LDFLAGS) -o $@ -shared

libgourou.so: libgourou.so.$(VERSION)
	ln -f -s $^ $@

libgourou.dylib.$(VERSION): $(OBJECTS) $(UPDFPARSERLIB)
	$(CXX) $^ $(LDFLAGS) -o $@ -shared

libgourou.dylib: libgourou.dylib.$(VERSION)
	ln -f -s $^ $@

build_utils: $(TARGET_LIBRARIES)
	$(MAKE) -C utils ROOT=$(PWD) CXX=$(CXX) AR=$(AR) DEBUG=$(DEBUG) STATIC_UTILS=$(STATIC_UTILS) DESTDIR=$(DESTDIR) PREFIX=$(PREFIX)

install: $(TARGET_LIBRARIES)
	install -d $(DESTDIR)$(PREFIX)$(LIBDIR)
# Use cp to preserver symlinks
	cp --no-dereference $(TARGET_LIBRARIES) $(DESTDIR)$(PREFIX)$(LIBDIR)
	$(MAKE) -C utils ROOT=$(PWD) CXX=$(CXX) AR=$(AR) DEBUG=$(DEBUG) STATIC_UTILS=$(STATIC_UTILS) DESTDIR=$(DESTDIR) PREFIX=$(PREFIX) install

uninstall:
	cd $(DESTDIR)$(PREFIX)/$(LIBDIR)
	rm -f $(TARGET_LIBRARIES) libgourou.so.$(VERSION)
	cd -

install_headers:
	install -d $(DESTDIR)$(PREFIX)/$(INCDIR)/libgourou
	cp --no-dereference include/*.h $(DESTDIR)$(PREFIX)/$(INCDIR)/libgourou

uninstall_headers:
	rm -rf $(DESTDIR)$(PREFIX)/$(INCDIR)/libgourou

clean:
	rm -rf libgourou.a libgourou.so libgourou.so.$(VERSION)* obj
	$(MAKE) -C utils clean

ultraclean: clean
	rm -rf lib
	$(MAKE) -C utils ultraclean
