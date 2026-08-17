APP     = Argus
VERSION = 0.1.2
OUT     = .build/$(APP)
BUNDLE  = $(HOME)/Applications/$(APP).app
BIN_DIR = $(HOME)/.local/bin
STAGE   = .build/stage
DIST    = dist/argus-$(VERSION)-macos-arm64.tar.gz
SHARE_FILES = share/chat.py share/ui.py share/ui.html share/settings.html \
	share/launch.py share/bridge.py share/prune.py

.PHONY: build test install uninstall clean dist

build:
	mkdir -p .build
	swiftc -O -o $(OUT) Argus/main.swift
	lipo $(OUT) -verify_arch arm64

test:
	python3 -m unittest discover -s tests -v
	python3 -m py_compile share/*.py
	zsh -n bin/argus
	sh -n install.sh

install: build
	mkdir -p $(BUNDLE)/Contents/MacOS $(BIN_DIR)
	cp $(OUT) $(BUNDLE)/Contents/MacOS/
	cp Info.plist $(BUNDLE)/Contents/
	plutil -replace CFBundleShortVersionString -string $(VERSION) $(BUNDLE)/Contents/Info.plist
	plutil -replace CFBundleVersion -string $(VERSION) $(BUNDLE)/Contents/Info.plist
	codesign --force --sign - $(BUNDLE)
	install -m 755 bin/argus $(BIN_DIR)/argus
	mkdir -p $(HOME)/.local/share/argus
	install -m 644 $(SHARE_FILES) $(HOME)/.local/share/argus/
	@echo "installed: $(BUNDLE) and $(BIN_DIR)/argus"
	@echo "open the tray app with: open $(BUNDLE)"

uninstall:
	-$(BIN_DIR)/argus bridge stop
	-$(BIN_DIR)/argus ui stop
	-$(BIN_DIR)/argus stop
	rm -rf $(BUNDLE)
	rm -f $(BIN_DIR)/argus
	rm -rf $(HOME)/.local/share/argus

dist: build
	rm -rf $(STAGE) && mkdir -p $(STAGE)/$(APP).app/Contents/MacOS $(STAGE)/bin $(STAGE)/share dist
	cp $(OUT) $(STAGE)/$(APP).app/Contents/MacOS/
	cp Info.plist $(STAGE)/$(APP).app/Contents/
	plutil -replace CFBundleShortVersionString -string $(VERSION) $(STAGE)/$(APP).app/Contents/Info.plist
	plutil -replace CFBundleVersion -string $(VERSION) $(STAGE)/$(APP).app/Contents/Info.plist
	codesign --force --sign - $(STAGE)/$(APP).app
	cp bin/argus $(STAGE)/bin/
	cp $(SHARE_FILES) $(STAGE)/share/
	cp install.sh README.md LICENSE $(STAGE)/
	chmod +x $(STAGE)/install.sh $(STAGE)/bin/argus
	tar -czf $(DIST) -C $(STAGE) .
	@echo "built $(DIST)"

clean:
	rm -rf .build dist
