#import "gw.h"
#import <spawn.h>
#import <sys/wait.h>
#import <unistd.h>
#import <string.h>
#import <stdarg.h>

extern char **environ;

NSString *const kProjectDir = @"/private/var/mobile/Documents/wiki.qaq.apelin/Projects/Gateway";
NSString *const kFallbackURL = @"http://127.0.0.1:15199";

static NSString *const kShell = @"/var/jb/bin/sh";
static NSString *const kCtl = @"/var/jb/usr/bin/gatewayctl";
static NSString *const kPathEnv = @"PATH=/var/jb/usr/local/bin:/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin:/usr/local/bin";

static NSString *gwLogPath(void) { return [kProjectDir stringByAppendingPathComponent:@"data/app.log"]; }
NSString *gwKeyPath(void) { return [kProjectDir stringByAppendingPathComponent:@"data/app.key"]; }

#pragma mark - màu và chấm

UIColor *gwGreen(void) { return [UIColor colorWithRed:0.24 green:0.86 blue:0.59 alpha:1]; }
UIColor *gwRed(void)   { return [UIColor colorWithRed:1.00 green:0.42 blue:0.42 alpha:1]; }
UIColor *gwAmber(void) { return [UIColor colorWithRed:1.00 green:0.78 blue:0.36 alpha:1]; }
UIColor *gwDim(void)   { return [UIColor colorWithWhite:0.55 alpha:1]; }

static UIColor *colorForKind(NSInteger kind) {
    switch (kind) {
        case 1: return gwGreen();
        case 2: return gwRed();
        case 3: return gwAmber();
        default: return gwDim();
    }
}

UIImage *gwDotImage(NSInteger kind) {
    CGFloat side = 12;
    UIGraphicsImageRenderer *r = [[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(side, side)];
    UIImage *img = [r imageWithActions:^(UIGraphicsImageRendererContext *ctx) {
        [colorForKind(kind) setFill];
        [[UIBezierPath bezierPathWithOvalInRect:CGRectMake(1, 1, side - 2, side - 2)] fill];
    }];
    return [img imageWithRenderingMode:UIImageRenderingModeAlwaysOriginal];
}

#pragma mark - chữ

NSString *gwTrim(NSString *s) {
    if (s == nil) return @"";
    return [s stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
}

/** Dòng cuối khác rỗng. */
static NSString *lastLine(NSString *text) {
    NSArray<NSString *> *lines = [text componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    for (NSInteger i = (NSInteger)lines.count - 1; i >= 0; i--) {
        NSString *s = gwTrim(lines[(NSUInteger)i]);
        if (s.length) return s;
    }
    return @"";
}

/** Một trong ba từ gatewayctl `state` dùng. */
NSString *gwStateWord(NSString *ctlOutput) {
    NSString *line = lastLine(ctlOutput);
    for (NSString *word in @[@"dang_chay", @"dang_treo", @"khong_chay"]) {
        if ([line hasSuffix:word]) return word;
    }
    return @"";
}

NSString *gwStateNow(void) { return gwStateWord(gwCtl(@[@"state"])); }

/** Chỉ giữ dòng cuối cùng toàn chữ, số, '-' và '_' (bỏ qua cảnh báo của python). */
static NSString *keyIn(NSString *text) {
    NSCharacterSet *ok = [NSCharacterSet characterSetWithCharactersInString:
                          @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"];
    NSArray<NSString *> *lines = [text componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    for (NSInteger i = (NSInteger)lines.count - 1; i >= 0; i--) {
        NSString *s = gwTrim(lines[(NSUInteger)i]);
        if (s.length < 20) continue;
        BOOL good = YES;
        for (NSUInteger k = 0; k < s.length; k++) {
            if (![ok characterIsMember:[s characterAtIndex:k]]) { good = NO; break; }
        }
        if (good) return s;
    }
    return @"";
}

static NSString *gKeyCache = nil;

NSString *gwAppKey(void) {
    if (gKeyCache.length) return gKeyCache;
    NSString *raw = [NSString stringWithContentsOfFile:gwKeyPath() encoding:NSUTF8StringEncoding error:NULL];
    NSString *k = keyIn(raw);
    if (k.length == 0) {
        // gatewayctl tự cấp key mới khi key đã lưu bị xoá hoặc bị tắt
        k = keyIn(gwCtl(@[@"appkey"]));
    }
    if (k.length) gKeyCache = k;
    return k ?: @"";
}

void gwForgetKey(void) { gKeyCache = nil; }

/** Đặt key mới cho app (sau khi khôi phục sao lưu, key có thể đã đổi). */
void gwSetAppKey(NSString *k) {
    NSString *t = gwTrim(k);
    if (t.length == 0) return;
    gKeyCache = t;
    [[t dataUsingEncoding:NSUTF8StringEncoding] writeToFile:gwKeyPath() atomically:YES];
}

NSString *gwProviderLabel(NSString *name) {
    NSString *n = gwTrim(name);
    if ([n isEqualToString:@"openai"])    return @"OpenAI";
    if ([n isEqualToString:@"anthropic"]) return @"Anthropic";
    if ([n isEqualToString:@"deepseek"])  return @"DeepSeek";
    if ([n isEqualToString:@"custom"])    return @"Tự nhập (custom)";
    return n.length ? n : @"(không tên)";
}

NSArray<NSDictionary *> *gwKnownProviders(void) {
    return @[
        @{@"name": @"openai",    @"label": @"OpenAI",           @"hint": @"sk-proj-… / sk-…"},
        @{@"name": @"anthropic", @"label": @"Anthropic",        @"hint": @"sk-ant-…"},
        @{@"name": @"deepseek",  @"label": @"DeepSeek",         @"hint": @"sk-… (deepseek.com)"},
        @{@"name": @"custom",    @"label": @"Tự nhập (custom)", @"hint": @"dán base URL riêng"},
    ];
}

NSString *gwAgo(double ts) {
    if (ts <= 0) return @"chưa";
    double d = [[NSDate date] timeIntervalSince1970] - ts;
    if (d < 0) d = 0;
    if (d < 60) return @"vừa xong";
    if (d < 3600) return [NSString stringWithFormat:@"%.0f phút trước", d / 60];
    if (d < 86400) return [NSString stringWithFormat:@"%.0f giờ trước", d / 3600];
    return [NSString stringWithFormat:@"%.0f ngày trước", d / 86400];
}

NSString *gwDur(double secs) {
    if (secs <= 0) return @"0 giây";
    if (secs < 90) return [NSString stringWithFormat:@"%.0f giây", secs];
    if (secs < 5400) return [NSString stringWithFormat:@"%.0f phút", secs / 60];
    if (secs < 172800) {
        int h = (int)(secs / 3600), m = (int)((secs - h * 3600) / 60);
        return m ? [NSString stringWithFormat:@"%d giờ %d phút", h, m] : [NSString stringWithFormat:@"%d giờ", h];
    }
    return [NSString stringWithFormat:@"%.0f ngày", secs / 86400];
}

void gwBG(void (^work)(void)) {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), work);
}

void gwMain(void (^work)(void)) { dispatch_async(dispatch_get_main_queue(), work); }

void gwOpenURL(NSString *url) {
    NSURL *u = [NSURL URLWithString:url];
    if (u == nil) return;
    [[UIApplication sharedApplication] openURL:u options:@{} completionHandler:nil];
}

#pragma mark - ghi nhật ký

void gwLog(NSString *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    NSString *msg = [[NSString alloc] initWithFormat:fmt arguments:ap];
    va_end(ap);

    static NSDateFormatter *df = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        df = [[NSDateFormatter alloc] init];
        df.dateFormat = @"HH:mm:ss";
    });
    NSString *line = [NSString stringWithFormat:@"%@ %@\n", [df stringFromDate:[NSDate date]], msg];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *path = gwLogPath();
    [fm createDirectoryAtPath:[path stringByDeletingLastPathComponent]
  withIntermediateDirectories:YES attributes:nil error:NULL];
    NSDictionary *attr = [fm attributesOfItemAtPath:path error:NULL];
    if (attr != nil && [attr fileSize] > 200000) [fm removeItemAtPath:path error:NULL];
    if (![fm fileExistsAtPath:path]) [fm createFileAtPath:path contents:nil attributes:nil];
    NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:path];
    if (fh == nil) return;
    @try {
        [fh seekToEndOfFile];
        [fh writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
        [fh closeFile];
    } @catch (NSException *e) {
    }
}

NSString *gwStack(void) {
    NSArray *st = [NSThread callStackSymbols];
    NSUInteger n = st.count < 12 ? st.count : 12;
    return [[st subarrayWithRange:NSMakeRange(0, n)] componentsJoinedByString:@" < "];
}

#pragma mark - chạy lệnh

/** Chạy một script bằng dash trong /var/jb, gom cả stdout và stderr. */
NSString *gwRun(NSString *script, NSArray<NSString *> *args) {
    int fds[2];
    if (pipe(fds) != 0) return @"Không mở được ống lệnh.";
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&fa, fds[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&fa, fds[0]);
    posix_spawn_file_actions_addclose(&fa, fds[1]);

    NSMutableArray<NSString *> *list = [NSMutableArray arrayWithObject:kShell];
    [list addObject:script];
    if (args.count) [list addObjectsFromArray:args];
    NSUInteger n = list.count;
    char **cargv = calloc(n + 1, sizeof(char *));
    for (NSUInteger i = 0; i < n; i++) cargv[i] = strdup(list[i].UTF8String);

    char *env[] = { (char *)kPathEnv.UTF8String,
                    (char *)"HOME=/var/mobile",
                    (char *)"LANG=C.UTF-8",
                    NULL };

    pid_t pid = 0;
    int rc = posix_spawn(&pid, kShell.UTF8String, &fa, NULL, cargv, env);
    posix_spawn_file_actions_destroy(&fa);
    close(fds[1]);

    NSMutableData *out = [NSMutableData data];
    if (rc == 0) {
        char buf[4096];
        ssize_t got;
        while ((got = read(fds[0], buf, sizeof buf)) > 0) {
            [out appendBytes:buf length:(NSUInteger)got];
        }
        int status = 0;
        waitpid(pid, &status, 0);
    } else {
        NSString *err = [NSString stringWithFormat:@"Không chạy được %@ (mã %d).\n", script, rc];
        [out appendData:[err dataUsingEncoding:NSUTF8StringEncoding]];
    }
    close(fds[0]);
    for (NSUInteger i = 0; i < n; i++) free(cargv[i]);
    free(cargv);
    NSString *text = [[NSString alloc] initWithData:out encoding:NSUTF8StringEncoding];
    return text ?: @"";
}

NSString *gwCtl(NSArray<NSString *> *args) { return gwRun(kCtl, args); }

#pragma mark - máy khách HTTP

@implementation GWA

+ (instancetype)shared {
    static GWA *one = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        one = [[GWA alloc] init];
        [one refresh];
    });
    return one;
}

- (void)refresh {
    NSString *base = @"";
    for (NSString *line in [gwCtl(@[@"url"]) componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
        NSString *s = gwTrim(line);
        if ([s hasPrefix:@"http://"] || [s hasPrefix:@"https://"]) base = s;
    }
    if (base.length == 0) base = kFallbackURL;
    while ([base hasSuffix:@"/"]) base = [base substringToIndex:base.length - 1];
    self.base = base;
    self.key = gwAppKey();
}

static id gwClean(id v) {
    if ([v isKindOfClass:[NSNull class]]) return @"";
    if ([v isKindOfClass:[NSDictionary class]]) {
        NSMutableDictionary *d = [NSMutableDictionary dictionaryWithCapacity:[v count]];
        for (id k in v) {
            id c = gwClean(v[k]);
            if (c != nil) d[k] = c;
        }
        return d;
    }
    if ([v isKindOfClass:[NSArray class]]) {
        NSMutableArray *a = [NSMutableArray arrayWithCapacity:[v count]];
        for (id e in v) {
            id c = gwClean(e);
            if (c != nil) [a addObject:c];
        }
        return a;
    }
    return v;
}

static NSString *messageIn(id json, NSInteger code) {
    if ([json isKindOfClass:[NSDictionary class]]) {
        id err = json[@"error"];
        if ([err isKindOfClass:[NSDictionary class]] && [err[@"message"] isKindOfClass:[NSString class]]) {
            NSString *m = err[@"message"];
            if ([m isEqualToString:@"invalid client key"]) return @"Key của app không hợp lệ.";
            return m;
        }
    }
    return [NSString stringWithFormat:@"Máy chủ trả về lỗi %ld.", (long)code];
}

- (void)request:(NSString *)method path:(NSString *)path query:(NSDictionary *)query body:(NSDictionary *)body
          retry:(BOOL)retry done:(void (^)(id json, NSError *err))done {
    if ([path hasPrefix:@"/gateway/models/policy"]) {
        static int dbgReq = 0;
        if (dbgReq < 200) { dbgReq++; gwLog(@"DBG req#%d %@ %@ st=%@", dbgReq, method, path, gwStack()); }
    }
    if (self.base.length == 0) [self refresh];
    NSMutableString *url = [NSMutableString stringWithFormat:@"%@%@", self.base ?: kFallbackURL, path];
    if (query.count) {
        BOOL first = YES;
        for (NSString *k in query) {
            id v = query[k];
            if (v == nil || [v isKindOfClass:[NSNull class]]) continue;
            [url appendFormat:@"%@%@=%@", first ? @"?" : @"&", k,
             [[NSString stringWithFormat:@"%@", v] stringByAddingPercentEncodingWithAllowedCharacters:
              [NSCharacterSet URLQueryAllowedCharacterSet]]];
            first = NO;
        }
    }
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:url]
                                                      cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                                  timeoutInterval:20];
    req.HTTPMethod = method;
    if (self.key.length) [req setValue:[@"Bearer " stringByAppendingString:self.key] forHTTPHeaderField:@"Authorization"];
    if (body != nil) {
        req.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:NULL];
        [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    }
    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:req
        completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
        NSInteger code = 0;
        if ([resp isKindOfClass:[NSHTTPURLResponse class]]) code = ((NSHTTPURLResponse *)resp).statusCode;
        id json = nil;
        if (data.length) json = gwClean([NSJSONSerialization JSONObjectWithData:data options:0 error:NULL]);
        if (code == 401 && retry) {
            gwForgetKey();
            [self refresh];
            gwMain(^{ [self request:method path:path query:query body:body retry:NO done:done]; });
            return;
        }
        NSError *out = nil;
        if (err != nil) {
            if (err.code == NSURLErrorCannotConnectToHost || err.code == NSURLErrorCannotFindHost) {
                out = [NSError errorWithDomain:@"gateway" code:err.code
                                      userInfo:@{NSLocalizedDescriptionKey: @"Cổng chưa chạy — hãy mở cổng."}];
            } else {
                out = err;
            }
        } else if (code >= 400 || (json != nil && json[@"error"] != nil)) {
            out = [NSError errorWithDomain:@"gateway" code:code
                                  userInfo:@{NSLocalizedDescriptionKey: messageIn(json, code)}];
        }
        gwMain(^{ done(json, out); });
    }];
    [task resume];
}

- (void)get:(NSString *)path query:(NSDictionary *)query done:(void (^)(id json, NSError *err))done {
    [self request:@"GET" path:path query:query body:nil retry:YES done:done];
}

- (void)send:(NSString *)method path:(NSString *)path body:(NSDictionary *)body
        done:(void (^)(id json, NSError *err))done {
    [self request:method path:path query:nil body:body retry:YES done:done];
}

@end

#pragma mark - ô của bảng

@interface GWCell : UITableViewCell
@property (nonatomic, strong) UISwitch *toggle;
@property (nonatomic, strong) UILabel *valueLabel;
@property (nonatomic, copy) void (^onToggle)(BOOL on);
- (void)useSwitch:(BOOL)use;
- (void)useValue:(NSString *)text;
@end

@implementation GWCell

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)rid {
    self = [super initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:rid];
    if (self) {
        _toggle = [[UISwitch alloc] init];
        _toggle.onTintColor = gwGreen();
        [_toggle addTarget:self action:@selector(switched) forControlEvents:UIControlEventValueChanged];
        _valueLabel = [[UILabel alloc] init];
        _valueLabel.font = [UIFont systemFontOfSize:15];
        _valueLabel.textColor = [UIColor secondaryLabelColor];
    }
    return self;
}

- (void)switched {
    {
        static int dbg = 0;
        if (dbg < 24) {
            dbg++;
            gwLog(@"DBG switched on=%d cell=%p sw=%p win=%d stack=%@", (int)self.toggle.on, (__bridge void *)self, (__bridge void *)self.toggle,
                  (int)(self.toggle.window != nil), [[NSThread callStackSymbols] componentsJoinedByString:@" | "]);
        }
    }
    NSLog(@"DBG switched on=%d cell=%p win=%d st=%@", (int)self.toggle.on, (__bridge void *)self,
          (int)(self.toggle.window != nil), gwStack());
    if (self.onToggle) self.onToggle(self.toggle.on);
}

- (void)useSwitch:(BOOL)use {
    self.accessoryView = use ? self.toggle : nil;
    self.accessoryType = UITableViewCellAccessoryNone;
}

- (void)useValue:(NSString *)text {
    self.valueLabel.text = text;
    [self.valueLabel sizeToFit];
    self.accessoryView = self.valueLabel;
    self.accessoryType = UITableViewCellAccessoryNone;
}

@end

#pragma mark - các lớp dữ liệu

@implementation GWRow
+ (instancetype)title:(NSString *)title sub:(NSString *)sub {
    GWRow *r = [[GWRow alloc] init];
    r.title = title;
    r.sub = sub;
    return r;
}
@end

@implementation GWSection
+ (instancetype)header:(NSString *)header footer:(NSString *)footer {
    GWSection *s = [[GWSection alloc] init];
    s.header = header;
    s.footer = footer;
    s.rows = [NSMutableArray array];
    return s;
}
@end

#pragma mark - bảng nền

@interface GWTableVC ()
@property (nonatomic, strong) UIView *hud;
@property (nonatomic, strong) UILabel *hudLabel;
@end

@implementation GWTableVC

- (instancetype)init { return [super initWithStyle:UITableViewStyleInsetGrouped]; }

- (void)viewDidLoad {
    [super viewDidLoad];
    self.sections = [NSMutableArray array];
    self.tableView.rowHeight = UITableViewAutomaticDimension;
    self.tableView.estimatedRowHeight = 58;
    UIRefreshControl *rc = [[UIRefreshControl alloc] init];
    [rc addTarget:self action:@selector(refresh) forControlEvents:UIControlEventValueChanged];
    self.refreshControl = rc;
    [self loadData];
}

- (void)refresh { [self loadData]; }

- (void)loadData { }

- (GWSection *)addSection:(NSString *)header footer:(NSString *)footer {
    GWSection *s = [GWSection header:header footer:footer];
    [self.sections addObject:s];
    return s;
}

- (GWRow *)addRow:(GWSection *)section title:(NSString *)title sub:(NSString *)sub {
    GWRow *r = [GWRow title:title sub:sub];
    [section.rows addObject:r];
    return r;
}

- (void)reload { [self loadData]; }

#pragma mark bảng

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return (NSInteger)self.sections.count; }

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section {
    return (NSInteger)self.sections[(NSUInteger)section].rows.count;
}

- (NSString *)tableView:(UITableView *)tv titleForHeaderInSection:(NSInteger)section {
    return self.sections[(NSUInteger)section].header;
}

- (NSString *)tableView:(UITableView *)tv titleForFooterInSection:(NSInteger)section {
    return self.sections[(NSUInteger)section].footer;
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip {
    GWCell *cell = [tv dequeueReusableCellWithIdentifier:@"gw"];
    if (cell == nil) cell = [[GWCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:@"gw"];
    GWRow *row = self.sections[(NSUInteger)ip.section].rows[(NSUInteger)ip.row];

    cell.textLabel.text = row.title;
    cell.textLabel.font = [UIFont systemFontOfSize:16 weight:UIFontWeightMedium];
    cell.textLabel.textColor = [UIColor labelColor];
    cell.detailTextLabel.text = row.sub;
    cell.detailTextLabel.textColor = [UIColor secondaryLabelColor];
    cell.detailTextLabel.numberOfLines = 2;
    cell.imageView.image = row.dotKind ? gwDotImage(row.dotKind) : nil;

    cell.onToggle = nil;
    if (row.hasSwitch) {
        {
            static int dbg = 0;
            if (dbg < 24) {
                dbg++;
                gwLog(@"DBG cfg '%@' want=%d had=%d cell=%p sw=%p", row.title, (int)row.switchOn,
                      (int)cell.toggle.on, (__bridge void *)cell, (__bridge void *)cell.toggle);
            }
        }
        if (cell.toggle.on != row.switchOn) cell.toggle.on = row.switchOn;
        [cell useSwitch:YES];
        GWRow *r = row;
        cell.onToggle = ^(BOOL on) {
            r.switchOn = on;            // hàng là nguồn sự thật: vẽ lại bảng không đảo ngược công tắc
            if (r.toggle) r.toggle(on);
        };
    } else if (row.value.length) {
        [cell useValue:row.value];
    } else {
        [cell useSwitch:NO];
        cell.accessoryView = nil;
        cell.accessoryType = row.chevron ? UITableViewCellAccessoryDisclosureIndicator
                                         : UITableViewCellAccessoryNone;
    }
    cell.selectionStyle = row.tap ? UITableViewCellSelectionStyleDefault : UITableViewCellSelectionStyleNone;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip {
    [tv deselectRowAtIndexPath:ip animated:YES];
    GWRow *row = self.sections[(NSUInteger)ip.section].rows[(NSUInteger)ip.row];
    if (row.tap) row.tap(row);
}

#pragma mark chờ và báo

- (void)busy:(NSString *)text {
    if (self.hud == nil) {
        UIView *hud = [[UIView alloc] initWithFrame:self.view.bounds];
        hud.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        hud.backgroundColor = [UIColor colorWithWhite:0 alpha:0.4];

        UIView *box = [[UIView alloc] initWithFrame:CGRectMake(0, 0, 190, 96)];
        box.center = CGPointMake(hud.bounds.size.width / 2, hud.bounds.size.height / 2);
        box.autoresizingMask = UIViewAutoresizingFlexibleLeftMargin | UIViewAutoresizingFlexibleRightMargin |
                               UIViewAutoresizingFlexibleTopMargin | UIViewAutoresizingFlexibleBottomMargin;
        box.backgroundColor = [UIColor secondarySystemGroupedBackgroundColor];
        box.layer.cornerRadius = 14;

        UIActivityIndicatorView *sp = [[UIActivityIndicatorView alloc] initWithActivityIndicatorStyle:UIActivityIndicatorViewStyleMedium];
        sp.center = CGPointMake(95, 34);
        [sp startAnimating];
        [box addSubview:sp];

        UILabel *lab = [[UILabel alloc] initWithFrame:CGRectMake(8, 58, 174, 30)];
        lab.textAlignment = NSTextAlignmentCenter;
        lab.font = [UIFont systemFontOfSize:13];
        lab.numberOfLines = 2;
        lab.textColor = [UIColor labelColor];
        [box addSubview:lab];

        [hud addSubview:box];
        self.hud = hud;
        self.hudLabel = lab;
    }
    self.hudLabel.text = text ?: @"Đang xử lý…";
    [self.view addSubview:self.hud];
}

- (void)done {
    [self.hud removeFromSuperview];
    self.hud = nil;
    self.hudLabel = nil;
    [self.refreshControl endRefreshing];
}

- (void)toast:(NSString *)text {
    UILabel *lab = [[UILabel alloc] init];
    lab.text = text;
    lab.font = [UIFont systemFontOfSize:14];
    lab.textColor = [UIColor whiteColor];
    lab.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.92];
    lab.textAlignment = NSTextAlignmentCenter;
    lab.numberOfLines = 3;
    lab.layer.cornerRadius = 10;
    lab.clipsToBounds = YES;
    CGSize size = [lab sizeThatFits:CGSizeMake(self.view.bounds.size.width - 60, 200)];
    lab.frame = CGRectMake(30, self.view.bounds.size.height - 140, self.view.bounds.size.width - 60, size.height + 22);
    lab.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleTopMargin;
    [self.view addSubview:lab];
    [UIView animateWithDuration:2.2 delay:0 options:UIViewAnimationOptionCurveEaseIn animations:^{
        lab.alpha = 0;
    } completion:^(BOOL finished) { [lab removeFromSuperview]; }];
}

- (void)report:(NSError *)err title:(NSString *)title {
    [self done];
    UIAlertController *ac = [UIAlertController alertControllerWithTitle:title ?: @"Không xong"
                                                              message:err.localizedDescription
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:ac animated:YES completion:nil];
}

- (void)fail:(NSString *)text {
    NSError *e = [NSError errorWithDomain:@"gateway" code:0 userInfo:@{NSLocalizedDescriptionKey: text}];
    [self report:e title:@"Không xong"];
}

- (void)ask:(NSString *)title options:(NSArray<NSString *> *)options run:(void (^)(NSInteger i))run {
    UIAlertController *ac = [UIAlertController alertControllerWithTitle:title message:nil
                                                       preferredStyle:UIAlertControllerStyleActionSheet];
    for (NSUInteger i = 0; i < options.count; i++) {
        NSString *t = options[i];
        [ac addAction:[UIAlertAction actionWithTitle:t style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) { if (run) run((NSInteger)i); }]];
    }
    [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    ac.popoverPresentationController.sourceView = self.view;
    ac.popoverPresentationController.sourceRect = CGRectMake(self.view.bounds.size.width / 2,
                                                             self.view.bounds.size.height / 2, 1, 1);
    [self presentViewController:ac animated:YES completion:nil];
}

- (void)prompt:(NSString *)title value:(NSString *)value run:(void (^)(NSString *text))run {
    UIAlertController *ac = [UIAlertController alertControllerWithTitle:title message:nil
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.text = value ?: @"";
        tf.autocorrectionType = UITextAutocorrectionTypeNo;
        tf.autocapitalizationType = UITextAutocapitalizationTypeNone;
        tf.clearButtonMode = UITextFieldViewModeWhileEditing;
    }];
    [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [ac addAction:[UIAlertAction actionWithTitle:@"Lưu" style:UIAlertActionStyleDefault
                                        handler:^(UIAlertAction *a) {
        UITextField *tf = ac.textFields.firstObject;
        if (run) run(gwTrim(tf.text));
    }]];
    [self presentViewController:ac animated:YES completion:nil];
}

- (void)push:(UIViewController *)vc { [self.navigationController pushViewController:vc animated:YES]; }

@end
